import torch
import torch.nn as nn
import torch.optim as optim

class MLP_classifier(nn.Module):
    def __init__(self, input_size, hidden_size, num_classes):
        super(MLP_classifier, self).__init__()
        # 1つ目の全結合層（入力 -> 隠れ層）
        self.fc1 = nn.Linear(input_size, hidden_size)
        # 活性化関数（ReLU）
        self.relu = nn.ReLU()
        # 2つ目の全結合層（隠れ層 -> 出力）
        self.fc2 = nn.Linear(hidden_size, num_classes)

    def forward(self, x):
        # 順伝播（Forward pass）の定義
        out = self.fc1(x)
        out = self.relu(out)
        out = self.fc2(out)
        return out
    
input_size = 10      # 入力特徴量の次元数
hidden_size = 50     # 隠れ層のノード数
num_classes = 2      # 分類するクラス数（例: 2値分類）
num_epochs = 100     # 学習のエポック数
learning_rate = 0.01 # 学習率