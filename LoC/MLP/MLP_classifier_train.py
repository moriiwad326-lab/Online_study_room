from MLP_classifier import input_size, hidden_size, num_classes
from MLP_classifier import MLP_classifier as mlp
import torch
import torch.nn as nn
import torch.optim as optim

num_epochs = 100     # 学習のエポック数
learning_rate = 0.01 # 学習率

# 学習用のダミーデータを生成 (100サンプル)
# X: 入力データ (100行 x 10列)
X_train = torch.randn(100, input_size)
# y: 正解ラベル (0または1の値を100個)
y_train = torch.randint(0, num_classes, (100,))

# モデルのインスタンス化
model = mlp(input_size, hidden_size, num_classes)

# 損失関数: 交差エントロピー誤差（分類問題によく使用されます）
criterion = nn.CrossEntropyLoss()

# 最適化手法: Adamオプティマイザ
optimizer = optim.Adam(model.parameters(), lr=learning_rate)