import torch
import torch.nn as nn
import torch.optim as optim

class MLP_classifier(nn.Module):
    def __init__(self, input_size, hidden_sizes, num_classes, dropout=0.0):
        """
        input_size:   入力特徴量の次元数
        hidden_sizes: 隠れ層のノード数のリスト（例: [50] なら隠れ層1つ、[256, 128] なら隠れ層2つ）
                      int を渡した場合は隠れ層1つとして扱う
        num_classes:  分類するクラス数
        dropout:      各隠れ層の後に挟む Dropout の確率（0.0 なら挿入しない）
        """
        super(MLP_classifier, self).__init__()

        # int で渡された場合は隠れ層1つのリストに正規化する
        if isinstance(hidden_sizes, int):
            hidden_sizes = [hidden_sizes]

        layers = []
        prev_size = input_size
        for hidden_size in hidden_sizes:
            # 全結合層（前の層 -> 隠れ層）
            layers.append(nn.Linear(prev_size, hidden_size))
            # 活性化関数（ReLU）
            layers.append(nn.ReLU())
            # 過学習対策の Dropout（層を深くしたときに有効）
            if dropout > 0.0:
                layers.append(nn.Dropout(dropout))
            prev_size = hidden_size
        # 出力層（最後の隠れ層 -> 出力）。hidden_sizes が空なら単層の線形分類器になる
        layers.append(nn.Linear(prev_size, num_classes))

        self.layers = nn.Sequential(*layers)

    def forward(self, x):
        # 順伝播（Forward pass）の定義
        return self.layers(x)

input_size = 10      # 入力特徴量の次元数
hidden_sizes = [50]  # 隠れ層のノード数（要素を増やすと層が深くなる）
num_classes = 2      # 分類するクラス数（例: 2値分類）
dropout = 0.0        # Dropout の確率
num_epochs = 100     # 学習のエポック数
learning_rate = 0.01 # 学習率
