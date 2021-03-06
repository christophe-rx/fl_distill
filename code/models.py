import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import numpy as np

from functools import partial

device = 'cuda' if torch.cuda.is_available() else 'cpu'

class VGG(nn.Module):

    def __init__(self, cfg, size=512, out=10):
        super(VGG, self).__init__()

        self.features = self.make_layers(cfg)
        self.classifier = nn.Sequential(
            #nn.Dropout(),
            nn.Linear(size, size),
            nn.ReLU(True),
            #nn.Dropout(),
            nn.Linear(size, size),
            nn.ReLU(True),
            nn.Linear(size, out),
        )
         # Initialize weights
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, np.sqrt(2. / n))
                m.bias.data.zero_()

    def forward(self, x):
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x

    def make_layers(self, cfg, batch_norm=False):
        layers = []
        in_channels = 3
        for v in cfg:
            if v == 'M':
                layers += [nn.MaxPool2d(kernel_size=2, stride=2)]
            else:
                conv2d = nn.Conv2d(in_channels, v, kernel_size=3, padding=1)
                if batch_norm:
                    layers += [conv2d, nn.BatchNorm2d(v), nn.ReLU(inplace=True)]
                else:
                    layers += [conv2d, nn.ReLU(inplace=True)]
                in_channels = v
        return nn.Sequential(*layers)

def vgg11s():
    return VGG([32, 'M', 64, 'M', 128, 128, 'M', 128, 128, 'M', 128, 128, 'M'], size=128)
  
def vgg16():
    return VGG([64, 64, 'M', 128, 128, 'M', 256, 256, 256, 'M', 512, 512, 512, 'M', 512, 512, 512, 'M'])



class Block(nn.Module):
    '''expand + depthwise + pointwise'''
    def __init__(self, in_planes, out_planes, expansion, stride, norm_layer):
        super(Block, self).__init__()
        self.stride = stride

        planes = expansion * in_planes
        self.conv1 = nn.Conv2d(in_planes, planes, kernel_size=1, stride=1, padding=0, bias=False)
        self.bn1 = norm_layer(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=stride, padding=1, groups=planes, bias=False)
        self.bn2 = norm_layer(planes)
        self.conv3 = nn.Conv2d(planes, out_planes, kernel_size=1, stride=1, padding=0, bias=False)
        self.bn3 = norm_layer(out_planes)

        self.shortcut = nn.Sequential()
        if stride == 1 and in_planes != out_planes:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=1, padding=0, bias=False),
                norm_layer(out_planes),
            )

    def forward(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = F.relu(self.bn2(self.conv2(out)))
        out = self.bn3(self.conv3(out))
        out = out + self.shortcut(x) if self.stride==1 else out
        return out


class MobileNetV2(nn.Module):
    # (expansion, out_planes, num_blocks, stride)

    def __init__(self, num_classes=10, norm_layer=nn.BatchNorm2d,shrink=1):
        super(MobileNetV2, self).__init__()
        # NOTE: change conv1 stride 2 -> 1 for CIFAR10
        self.norm_layer = norm_layer
        self.cfg = [(1,  16//shrink, 1, 1),
                   (6,  24//shrink, 2, 1),  # NOTE: change stride 2 -> 1 for CIFAR10
                   (6,  32//shrink, 3, 2),
                   (6,  64//shrink, 4, 2),
                   (6,  96//shrink, 3, 1),
                   (6, 160//shrink, 3, 2),
                   (6, 320//shrink, 1, 1)]


        self.conv1 = nn.Conv2d(3, 32, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = self.norm_layer(32)
        self.layers = self._make_layers(in_planes=32)
        self.conv2 = nn.Conv2d(self.cfg[-1][1], 1280//shrink, kernel_size=1, stride=1, padding=0, bias=False)
        self.bn2 = self.norm_layer(1280//shrink)
        self.linear = nn.Linear(1280//shrink, num_classes)


    def _make_layers(self, in_planes):
        layers = []
        for expansion, out_planes, num_blocks, stride in self.cfg:
            strides = [stride] + [1]*(num_blocks-1)
            for stride in strides:
                layers.append(Block(in_planes, out_planes, expansion, stride, self.norm_layer))
                in_planes = out_planes
        return nn.Sequential(*layers)

    def forward(self, x):
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.layers(out)
        out = F.relu(self.bn2(self.conv2(out)))
        # NOTE: change pooling kernel_size 7 -> 4 for CIFAR10
        out = F.avg_pool2d(out, 4)
        out = out.view(out.size(0), -1)
        out = self.linear(out)
        return out

def mobilenetv2():
    return MobileNetV2(norm_layer=nn.BatchNorm2d)

def mobilenetv2s():
    return MobileNetV2(norm_layer=nn.BatchNorm2d, shrink=2)

def mobilenetv2xs():
    return MobileNetV2(norm_layer=nn.BatchNorm2d, shrink=4)

def mobilenetv2_gn():
    return MobileNetV2(norm_layer=lambda x : nn.GroupNorm(num_groups=2, num_channels=x))
    





class lenet_cifar(nn.Module):
    def __init__(self):
        super(lenet_cifar, self).__init__()
        self.conv1 = nn.Conv2d(3, 6, 5)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(6, 16, 5)
        self.fc1 = nn.Linear(16 * 5 * 5, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, 10)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = x.view(-1, 16 * 5 * 5)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x


class lenet_mnist(torch.nn.Module):
    def __init__(self):
        super(lenet_mnist, self).__init__()
        self.conv1 = torch.nn.Conv2d(1, 6, 5)
        self.pool = torch.nn.MaxPool2d(2, 2)
        self.conv2 = torch.nn.Conv2d(6, 16, 5)
        self.fc1 = torch.nn.Linear(16 * 4 * 4, 120)
        self.fc2 = torch.nn.Linear(120, 84)
        self.fc3 = torch.nn.Linear(84, 62)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = x.view(-1, 16 * 4 * 4)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x



def get_model(model):

  return  { "vgg16" : (vgg16, optim.SGD, {"lr":0.04, "momentum":0.9, "weight_decay":5e-5}),
            "vgg11s" : (vgg11s, optim.SGD, {"lr":0.04, "momentum":0.9, "weight_decay":5e-5}),
              "lenet_cifar" : (lenet_cifar, optim.SGD, {"lr":0.01, "weight_decay":0.0}),
              "lenet_mnist" : (lenet_mnist, optim.Adam, {"lr":0.001, "weight_decay":0.0}),
              "mobilenetv2" : (mobilenetv2, optim.SGD, {"lr" : 0.01, "momentum" :0.9, "weight_decay" :5e-4}),
              "mobilenetv2s" : (mobilenetv2s, optim.SGD, {"lr" : 0.01, "momentum" :0.9, "weight_decay" :5e-4}),
              "mobilenetv2xs" : (mobilenetv2xs, optim.SGD, {"lr" : 0.01, "momentum" :0.9, "weight_decay" :5e-4}),
              "mobilenetv2_gn" : (mobilenetv2_gn, optim.SGD, {"lr" : 0.01, "momentum" :0.9, "weight_decay" :5e-4})
          }[model]


def print_model(model):
  n = 0
  print("Model:")
  for key, value in model.named_parameters():
    print(' -', '{:30}'.format(key), list(value.shape))
    n += value.numel()
  print("Total number of Parameters: ", n) 
  print()












