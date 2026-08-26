import torch
import torch.nn as nn
import numpy as np


# ==========================================
# 1. ダミーデータの作成（サイン波）
# ==========================================
def make_sine_dataset(seq_length: int = 20, num_points: int = 1000):
    """サイン波から (X, Y) のダミー系列データを作る（PoC用）。

    LoCモデル（`core.py`）から `SimpleLSTM` を import した際に乱数シードの固定や
    ダミーデータ生成が副作用として走らないよう、関数の中に閉じてある。
    """
    # 乱数シードの固定（再現性のため）
    torch.manual_seed(42)
    np.random.seed(42)

    # 0から100までの範囲で num_points 個のデータポイントを作成
    data = np.sin(np.linspace(0, 100, num_points))

    X, Y = [], []
    for i in range(len(data) - seq_length):
        X.append(data[i : i + seq_length])
        Y.append(data[i + seq_length])

    # PyTorchのLSTMは入力を (バッチサイズ, シーケンス長, 入力特徴量数) にする必要がある
    X = torch.tensor(np.array(X), dtype=torch.float32).unsqueeze(-1)  # 形: (980, 20, 1)
    Y = torch.tensor(np.array(Y), dtype=torch.float32).unsqueeze(-1)  # 形: (980, 1)
    return X, Y


# ==========================================
# 2. LSTMモデルの定義
# ==========================================
class SimpleLSTM(nn.Module):
    def __init__(self, input_size, hidden_size, num_layers, output_size):
        super(SimpleLSTM, self).__init__()
        self.hidden_size = hidden_size
        
        # batch_first=True にすると入力が (Batch, Seq, Feature) になる
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        
        # LSTMの出力を予測値に変換する全結合層
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x):
        # xの形: (batch_size, seq_length, input_size)
        
        # LSTM層を通す
        # out: すべてのタイムステップの隠れ状態 (batch_size, seq_length, hidden_size)
        # hn: 最後のタイムステップの隠れ状態
        # cn: 最後のタイムステップのセル状態
        out, (hn, cn) = self.lstm(x)
        
        # 予測には最後のタイムステップの出力だけを使用する
        last_out = out[:, -1, :] # 形: (batch_size, hidden_size)
        
        # 全結合層で最終的な予測値を出力
        pred = self.fc(last_out)
        return pred
    
input_size = 1   # 入力は1次元（サイン波の各時点の値）
hidden_size = 16 # LSTMの隠れ層の次元数（記憶容量のようなもの）
num_layers = 1   # LSTM層の数（深くすると複雑なパターンを学習できる）
output_size = 1  # 出力は1次元（次の時点のサイン波の値）