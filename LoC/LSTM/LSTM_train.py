from LSTM import SimpleLSTM, make_sine_dataset, input_size, hidden_size, num_layers, output_size
import torch
import torch.nn as nn
import numpy as np

X, Y = make_sine_dataset(seq_length=20)

model = SimpleLSTM(input_size, hidden_size, num_layers, output_size)

# 損失関数（回帰問題なのでMSELoss）と最適化手法（Adam）
criterion = nn.MSELoss()
optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

# ==========================================
# 4. 学習ループ
# ==========================================
epochs = 100

for epoch in range(epochs):
    model.train()           # モデルを学習モードに設定
    optimizer.zero_grad()   # 勾配をリセット
    
    # 順伝播（予測）
    output = model(X)
    
    # 損失の計算
    loss = criterion(output, Y)
    
    # 逆伝播（勾配の計算）とパラメータ更新
    loss.backward()
    optimizer.step()
    
    # 20エポックごとに経過を表示
    if (epoch + 1) % 20 == 0:
        print(f'Epoch [{epoch+1}/{epochs}], Loss: {loss.item():.4f}')

print("学習が完了しました！")