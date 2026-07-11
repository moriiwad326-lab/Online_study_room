import os
import numpy as np
import torch
import inspect

from datasets import load_dataset
import evaluate

from transformers import (
    AutoImageProcessor,
    AutoModelForImageClassification,
    TrainingArguments,
    Trainer,
)
from torchvision import transforms
from torchvision.transforms import AutoAugment, AutoAugmentPolicy
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.metrics import confusion_matrix, classification_report
import pandas as pd
from torchvision.transforms import RandAugment
from PIL import ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

# val_tf の Resize も size に合わせる
val_tf = transforms.Compose([
    transforms.Resize(size + 32),
    transforms.CenterCrop(size),
    transforms.ToTensor(),
    transforms.Normalize(mean=image_mean, std=image_std),
])

def with_transform_train(batch):
    images = [img.convert("RGB") for img in batch["image"]]
    batch["pixel_values"] = [train_tf(img) for img in images]
    return batch

def with_transform_val(batch):
    images = [img.convert("RGB") for img in batch["image"]]
    batch["pixel_values"] = [val_tf(img) for img in images]
    return batch

def collate_fn(examples):
    pixel_values = torch.stack([ex["pixel_values"] for ex in examples])
    labels = torch.tensor([ex["label"] for ex in examples], dtype=torch.long)
    return {"pixel_values": pixel_values, "labels": labels}

def do_finetune():
    # =========================
    # 設定
    # =========================

    #data_dir = "./data/UECFOOD256"
    data_dir = "./data/combined_food/train"
    model_ckpt = "google/vit-base-patch16-384"
    output_dir = "./vit-uec256-finetuned-weights"

    seed = 42
    torch.manual_seed(seed)
    np.random.seed(seed)

    # =========================
    # データ読み込み
    # =========================
    # 例: data_dir 配下に train/ と val/ がある場合
    ds = load_dataset("imagefolder", data_dir=data_dir)

    # val が無い場合は train から分割
    if "validation" not in ds:
        split = ds["train"].train_test_split(test_size=0.1, seed=seed)
        ds = {"train": split["train"], "validation": split["test"]}

    # クラス名（ラベル名）取得
    label_names = ds["train"].features["label"].names
    num_labels = len(label_names)
    if num_labels != 256:
        print(f"[WARN] クラス数が 256 ではありません: num_labels={num_labels}")

    label2id = {name: i for i, name in enumerate(label_names)}
    id2label = {i: name for i, name in enumerate(label_names)}

    # 指定したクラス名のディレクトリを学習から除外する（data_dir 配下のフォルダ名がクラス名になっている想定）
    exclude_names = ["1", "36"]
    exclude_ids = [label_names.index(n) for n in exclude_names if n in label_names]
    if exclude_ids:
        print(f"Excluding classes: names={exclude_names} ids={exclude_ids}")
        ds["train"] = ds["train"].filter(lambda ex: ex["label"] not in exclude_ids)
        ds["validation"] = ds["validation"].filter(lambda ex: ex["label"] not in exclude_ids)

        # 除外後にラベルのギャップができるため、ラベル名リストを再構築して新しい連番ラベルにリマップする
        kept_label_names = [n for n in label_names if n not in exclude_names]
        new_label2id = {name: i for i, name in enumerate(kept_label_names)}
        # id2label / label2id を新しい連番に置き換える
        id2label = {i: name for i, name in enumerate(kept_label_names)}
        label2id = new_label2id

        # データセット中の古いラベル(int)を新しい連番ラベルに置換する
        def _remap_label(example):
            old = example["label"]
            name = label_names[old]
            return {"label": new_label2id[name]}

        ds["train"] = ds["train"].map(_remap_label)
        ds["validation"] = ds["validation"].map(_remap_label)

        # 新しいクラス数
        num_labels = len(kept_label_names)
    # =========================
    # Processor / transforms
    # =========================
    processor = AutoImageProcessor.from_pretrained(model_ckpt)
    image_mean = processor.image_mean
    image_std = processor.image_std

    # ViT入力サイズ（通常224）
    if isinstance(processor.size, dict) and "height" in processor.size:
        size = processor.size["height"]
    else:
        # 互換用
        size = 384

    ds_train = ds["train"].with_transform(with_transform_train)
    ds_val = ds["validation"].with_transform(with_transform_val)

    # =========================
    # モデル（分類ヘッドを256出力に差し替えてロード）
    # =========================
    model = AutoModelForImageClassification.from_pretrained(
        model_ckpt,
        num_labels=num_labels,
        id2label=id2label,
        label2id=label2id,
        ignore_mismatched_sizes=True,  # ←ImageNetの1000クラスヘッドを読み捨てて付け替える
    )

    # （任意）最初はバックボーンを凍結してヘッドだけ学習 → 途中で全体を解凍、なども可能
    # for p in model.vit.parameters():
    #     p.requires_grad = False

    # =========================
    # 評価指標
    # =========================
    acc = evaluate.load("accuracy")

    def compute_metrics(eval_pred):
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=1)
        return acc.compute(predictions=preds, references=labels)
    
    # =========================
    # 学習設定
    # =========================
    # Build TrainingArguments kwargs dynamically to support older/newer transformers
    args_kwargs = dict(
        output_dir=output_dir,
        # 384pxはメモリを食うため、16程度に落とすのが安全です
        per_device_train_batch_size=16, 
        per_device_eval_batch_size=16,
        # バッチサイズを下げた分、4ステップ貯めてから更新することで実質バッチサイズ64(16*4)にする
        gradient_accumulation_steps=4, 
        learning_rate=1e-5,           # 高解像度なので低めの学習率でじっくり
        lr_scheduler_type="cosine",
        num_train_epochs=30,
        weight_decay=0.05,
        warmup_ratio=0.1,
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
        logging_steps=50,
        logging_dir="./logs",
        fp16=torch.cuda.is_available(),
        report_to="tensorboard",
        dataloader_num_workers=4,
        remove_unused_columns=False,
        seed=seed,
        label_smoothing_factor=0.1,
    )

    # Only include kwargs that TrainingArguments.__init__ accepts
    sig = inspect.signature(TrainingArguments.__init__)
    # If TrainingArguments supports evaluation/save strategy, set both to 'epoch'.
    # Otherwise disable load_best_model_at_end to avoid validation errors in older versions.
    if "evaluation_strategy" in sig.parameters:
        args_kwargs["evaluation_strategy"] = "epoch"
        if "save_strategy" in sig.parameters:
            args_kwargs["save_strategy"] = "epoch"
    else:
        if "load_best_model_at_end" in args_kwargs:
            args_kwargs["load_best_model_at_end"] = False

    args = TrainingArguments(**args_kwargs)

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=ds_train,
        eval_dataset=ds_val,
        tokenizer=processor,     # 互換のため渡しておく（必須ではない）
        data_collator=collate_fn,
        compute_metrics=compute_metrics,
    )

    print("\n" + "="*70)
    print("TensorBoard を起動して学習の収束をリアルタイムで監視できます：")
    print("  $ tensorboard --logdir=./logs")
    print("  ブラウザで http://localhost:6006 にアクセス")
    print("="*70 + "\n")

    trainer.train()

    analyze_results(trainer, ds_val, id2label)

    metrics = trainer.evaluate()
    print("Final eval:", metrics)

    trainer.save_model(output_dir)
    processor.save_pretrained(output_dir)
    print(f"Saved to: {output_dir}")

def analyze_results(trainer, eval_dataset, id2label):
    print("--- Analyzing Results ---")
    
    # 1. 予測の実行 (Validationデータを使用)
    output = trainer.predict(eval_dataset)
    y_true = output.label_ids
    y_pred = np.argmax(output.predictions, axis=-1)

    # 2. クラスごとの精度レポートを作成
    # target_namesをID順に並べる
    target_names = [id2label[i] for i in range(len(id2label))]
    report = classification_report(y_true, y_pred, target_names=target_names, output_dict=True)
    df_report = pd.DataFrame(report).transpose()
    
    # 3. 再現率(recall)が低いワースト10を抽出
    # recallが低い = その料理を別の料理だと間違えて判定している
    print("\n[ワースト10クラス (精度順)]")
    worst_10 = df_report.iloc[:-3, :].sort_values(by="recall").head(10) # 最後3行は全体平均なので除外
    print(worst_10[["recall", "f1-score"]])

    # 4. 混同行列の計算
    cm = confusion_matrix(y_true, y_pred)
    
    # 5. 間違いやすいペア TOP 10 を抽出
    confused_pairs = []
    for i in range(len(cm)):
        for j in range(len(cm)):
            if i != j and cm[i][j] > 0:
                confused_pairs.append({
                    "true_label": id2label[i],
                    "pred_label": id2label[j],
                    "count": cm[i][j]
                })
    
    df_confused = pd.DataFrame(confused_pairs).sort_values(by="count", ascending=False)
    print("\n[間違いやすいペア TOP 10]")
    print(df_confused.head(10))

    # 6. 可視化（画像として保存）
    # クラスが多いので、上位の間違いに絞ってプロット
    worst_indices = [target_names.index(name) for name in worst_10.index]
    plt.figure(figsize=(14, 10))
    sns.heatmap(cm[worst_indices][:, worst_indices], annot=True, fmt='d',
                xticklabels=[target_names[i] for i in worst_indices],
                yticklabels=[target_names[i] for i in worst_indices],
                cmap='YlGnBu')
    plt.title("Confusion Matrix (Worst 10 Classes)")
    plt.xlabel("Predicted")
    plt.ylabel("True")
    plt.tight_layout()
    plt.savefig("confusion_matrix.png") # 画面に出ない場合に備えて画像保存
    print("\nConfusion matrix saved as 'confusion_matrix.png'")
    plt.show()

if __name__ == "__main__":
    do_finetune()