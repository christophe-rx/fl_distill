import random
from tqdm import tqdm

import torch
import torch.optim as optim
import torch.nn as nn
from torch.utils.data import ConcatDataset, TensorDataset, DataLoader

from virtual_adversarial_training import VATLoss
from data import DataloaderMerger

import numpy as np



device = 'cuda' if torch.cuda.is_available() else 'cpu'
    

class Device(object):
  def __init__(self, model_fn, optimizer_fn, loader, distill_loader, init=None):
    self.model = model_fn().to(device)
    self.loaders = DataloaderMerger({'base': loader})
    self.distill_loader = distill_loader


    self.W = {key : value for key, value in self.model.named_parameters()}
    self.dW = {key : torch.zeros_like(value) for key, value in self.model.named_parameters()}
    self.W_old = {key : torch.zeros_like(value) for key, value in self.model.named_parameters()}

    self.optimizer_fn = optimizer_fn
    self.optimizer = optimizer_fn(self.model.parameters())   
    self.scheduler = optim.lr_scheduler.StepLR(self.optimizer, step_size=1, gamma=0.96)  
    
  def evaluate(self, loader=None):
    return eval_op(self.model, self.loaders if not loader else loader)

  def save_model(self, path=None, name=None, verbose=True):
    if name:
      torch.save(self.model.state_dict(), path+name)
      if verbose: print("Saved model to", path+name)

  def load_model(self, path=None, name=None, verbose=True):
    if name:
      self.model.load_state_dict(torch.load(path+name))
      if verbose: print("Loaded model from", path+name)

  def distill(self, clients=None, epochs=1, mode="regular", distill_phase="server"):
    print("Distilling...")

    if distill_phase == "server":
      self.model.train()
    elif distill_phase == "clients":
      epochs = 1 # for the one-step distillation only the softlabels are necessary

    #vat_loss = VATLoss(xi=10.0, eps=1.0, ip=1)

    assert mode in ["regular", "weighted", "pate", "knn", "pate_up"], "mode has to be one of [regular, pate, knn]"

    acc = 0
    softlabels = []
    for ep in range(epochs):
      running_loss, samples = 0.0, 0
      for x, _ in tqdm(self.distill_loader):   
        x = x.to(device)

        if mode == "regular":
          y = torch.zeros([x.shape[0], 10], device=device)
          for i, client in enumerate(clients):
            y_p = client.predict(x)
            y += (y_p/len(clients)).detach()

        if mode == "weighted":
          y = torch.zeros([x.shape[0], 10], device=device)
          w = torch.zeros([x.shape[0], 1], device=device)
          for i, client in enumerate(clients):
            y_p = client.predict(x)
            weight = client.predict_knn_weight(x).view(-1,1)

            y += (y_p*weight).detach()
            w += weight.detach()
          y = y / w


        if mode == "pate":
          hist = torch.sum(torch.stack([client.predict_(x) for client in clients]), dim=0)
          #hist += torch.randn_like(hist)

          amax = torch.argmax(hist, dim=1)

          y = torch.zeros_like(hist)
          y[torch.arange(hist.shape[0]),amax] = 1


        if mode == "pate_up":
          y = torch.mean(torch.stack([client.predict_(x) for client in clients]), dim=0)


        if mode in "knn":
          scores = torch.zeros([x.shape[0], 10], device=device)
          for i, client in enumerate(clients):
            y_p = client.predict_knn(x, k=10)
            scores += (y_p/len(clients)).detach()          

          amax = torch.argmax(scores, dim=1)

          y = torch.zeros_like(scores)
          y[torch.arange(x.shape[0]),amax] = 1

        if distill_phase == "server":
          self.optimizer.zero_grad()
          #vat = vat_loss(model, x)
          y_ = nn.Softmax(1)(self.model(x))

          def kulbach_leibler_divergence(predicted, target):
            return -(target * torch.log(predicted.clamp_min(1e-7))).sum(dim=-1).mean() - \
                   -1*(target.clamp(min=1e-7) * torch.log(target.clamp(min=1e-7))).sum(dim=-1).mean()

          loss = kulbach_leibler_divergence(y_,y)#torch.mean(torch.sum(y_*(y_.log()-y.log()), dim=1))

 
          running_loss += loss.item()*y.shape[0]
          samples += y.shape[0]

          loss.backward()
          self.optimizer.step()
        else:
          softlabels.append(y)

      if distill_phase == "server":
        acc_new = eval_op(self.model, self.loaders)["accuracy"]
        print(acc_new)

        if acc_new < acc:
          return {"loss" : running_loss / samples, "acc" : acc_new, "epochs" : ep}
        else:
          acc = acc_new

    # update clients with new softlabel data
    if distill_phase == "clients":
      softlabels = torch.cat(softlabels)
      batch_size = self.kwargs["batch_size"] - int(self.kwargs["local_data_percentage"]*self.kwargs["batch_size"])
      distill_client_loader = DataLoader(TensorDataset(self.aux_data, softlabels.to('cpu')), batch_size=batch_size, shuffle=True)
      for client in clients:
        client.set_combined_dataloader(loader=distill_client_loader)
        print(np.max([i for i,x in enumerate(client.loaders)]))
      return {"loss" : 1, "acc" : [eval_op(c.model, self.loaders)["accuracy"] for c in self.clients], "epochs" : 1}

    return {"loss" : running_loss / samples, "acc" : acc_new, "epochs" : ep}
      
class Client(Device):
  def __init__(self, model_fn, optimizer_fn, loader=None, aux_data=None, distill_loader=None, init=None, **kwargs):
    super().__init__(model_fn, optimizer_fn, loader, distill_loader, init)
    print(np.max([i for i,x in enumerate(self.loaders)]))
    self.kwargs = kwargs
      
  def set_combined_dataloader(self, loader):
    self.loaders['aux_loader'] = loader

  def synchronize_with_server(self, server):
    self.model.load_state_dict(server.model.state_dict())
    #copy(target=self.W, source=server.W)
    
  def compute_weight_update(self, epochs=1, loader=None, **kwargs):
    train_stats = train_op(self.model, self.loaders if not loader else loader, self.optimizer, self.scheduler, epochs, **kwargs)
    return train_stats

  def predict(self, x):
    """Softmax prediction on input"""
    self.model.eval()
    with torch.no_grad():
      y_ = nn.Softmax(1)(self.model(x))

    return y_


  def predict_(self, x):
    """Argmax prediction on input"""
    self.model.eval()
    with torch.no_grad():
      y_ = nn.Softmax(1)(self.model(x))
      
      amax = torch.argmax(y_, dim=1).detach()#(torch.cumsum(y_, dim=1)<torch.rand(size=(y_.shape[0],1))).sum(dim=1)

      t = torch.zeros_like(y_)
      t[torch.arange(y_.shape[0]),amax] = 1

    return t.detach()


  def generate_feature_bank(self):
    """Extracts Features of local data using local neural network"""
    feature_bank, feature_labels = [], []
    with torch.no_grad():
        # generate feature bank
        for data, target, source in self.loaders:
            feature = self.model.f(data.to(device)).squeeze() 
            #feature = feature / torch.norm(feature, dim=1).view(-1,1)
            feature_bank.append(feature)
            feature_labels.append(target)
        # [D, N]
        self.feature_bank = torch.cat(feature_bank, dim=0).t().contiguous()
        # [N]
        self.feature_labels = torch.cat(feature_labels)
        # loop test data to predict the label by weighted knn search

  def predict_knn_weight(self, x, k=10):
    """Weighted k-neirest-neighbor-prediction in feature space (first a feature bank needs to be generated)"""
    with torch.no_grad():
      x = x.to(device)
      feature = self.model.f(x).squeeze()
      #feature = feature / torch.norm(feature, dim=1).view(-1,1)

      # compute cos similarity between each feature vector and feature bank ---> [B, N]
      sim_matrix = torch.mm(feature, self.feature_bank)
      # [B, K]
      sim_weight, sim_indices = sim_matrix.topk(k=k, dim=-1)

    return torch.mean(sim_weight,axis=-1)


  def predict_knn(self, x, k=10, n_classes=10):
    """Weighted k-neirest-neighbor-prediction in feature space (first a feature bank needs to be generated)"""
    with torch.no_grad():
      x = x.to(device)
      feature = self.model.f(x).squeeze()
      feature = feature / torch.norm(feature, dim=1).view(-1,1)

      # compute cos similarity between each feature vector and feature bank ---> [B, N]
      sim_matrix = torch.mm(feature, self.feature_bank)
      # [B, K]
      sim_weight, sim_indices = sim_matrix.topk(k=k, dim=-1)
      # [B, K]
      sim_labels = torch.gather(self.feature_labels.expand(x.size(0), -1).to(device), dim=-1, index=sim_indices.to(device))
      #sim_weight = (sim_weight / temperature).exp()

      # counts for each class
      one_hot_label = torch.zeros(x.size(0) * k, n_classes, device=sim_labels.device)
      # [B*K, C]
      one_hot_label = one_hot_label.scatter(dim=-1, index=sim_labels.long().view(-1, 1), value=1.0)
      # weighted score ---> [B, C]
      pred_scores = torch.sum(one_hot_label.view(x.size(0), -1, n_classes) * sim_weight.unsqueeze(dim=-1), dim=1)

    return pred_scores.detach()



  def compute_prediction_matrix(self, distill_loader):

    predictions = []
    for x, _ in distill_loader:
      x = x.to(device)
      y_predict = self.predict(x).detach()
      
      predictions += [y_predict]

    return torch.cat(predictions, dim=0).detach().cpu().numpy()

   
 
class Server(Device):
  def __init__(self, model_fn, optimizer_fn, loader, distill_loader, aux_data=None, init=None, clients=None, **kwargs):
    super().__init__(model_fn, optimizer_fn, loader, distill_loader, init)
    self.clients = clients
    self.aux_data = aux_data
    self.kwargs = kwargs
    
  def select_clients(self, clients, frac=1.0):
    return random.sample(clients, int(len(clients)*frac)) 
    
  def aggregate_weight_updates(self, clients):
    reduce_average(target=self.W, sources=[client.W for client in clients])



def train_op(model, loaders, optimizer, scheduler, epochs, **kwargs):
    model.train()  
    running_loss, samples = 0.0, 0
    for ep in range(epochs):
      for x, y, source in loaders:
        x, y, source = x.to(device), y.to(device), source.to(device)
        
        optimizer.zero_grad()

        loss = nn.BCEWithLogitsLoss(reduction='none')(model(x), y)
        local_loss = loss[source == 0]

        local_loss = local_loss.sum()
        distill_loss = 0
        if len(source.nonzero()) < 1:
          distill_loss = loss[source != 0].sum()

        loss = local_loss + kwargs["distill_weight"] * warmup(kwargs["c_round"], kwargs["max_c_round"]) * distill_loss
        loss = loss / x.size(0)

        running_loss += loss.item()*y.shape[0]
        samples += y.shape[0]

        loss.backward()
        optimizer.step() 
      #scheduler.step()

    return {"loss" : running_loss / samples}

def warmup(curr, max, type='constant'):
  if type == 'constant':
    return 1
  elif type == 'tanh':
    return np.tanh(curr/(0.5*max))

def eval_op(model, loaders):
    model.train()
    samples, correct = 0, 0

    with torch.no_grad():
      for x, y, source in loaders:
        x, y = x.to(device), y.to(device)

        y_ = model(x)
        _, predicted = torch.max(y_.detach(), 1)
        
        samples += y.shape[0]
        correct += (predicted == torch.argmax(y, dim=1)).sum().item()

    return {"accuracy" : correct/samples}



def flatten(source):
  return torch.cat([value.flatten() for value in source.values()])

def copy(target, source):
  for name in target:
    target[name].data = source[name].detach().clone()
    
def reduce_average(target, sources):
  for name in target:
      target[name].data = torch.mean(torch.stack([source[name].detach() for source in sources]), dim=0).clone()

def subtract_(target, minuend, subtrahend):
  for name in target:
    target[name].data = minuend[name].detach().clone()-subtrahend[name].detach().clone()



