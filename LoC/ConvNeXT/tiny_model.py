import argparse
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForImageClassification
from transformers.models.convnext.image_processing_pil_convnext import (
    ConvNextImageProcessorPil,
)

MODEL_NAME = "facebook/convnext-tiny-224"


def load_model_and_processor(model_name: str = MODEL_NAME):
    processor = ConvNextImageProcessorPil.from_pretrained(model_name)

    model = AutoModelForImageClassification.from_pretrained(model_name)
    model.eval()
    return processor, model


def get_raw_output(image_path: str, model_name: str = MODEL_NAME):
    processor, model = load_model_and_processor(model_name)

    image = Image.open(image_path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")

    with torch.no_grad():
        outputs = model(**inputs)

    logits = outputs.logits[0].cpu().numpy()
    return logits


def main():
    parser = argparse.ArgumentParser(description="ConvNeXt で 224x224 画像の生の出力を取得")
    parser.add_argument("image", help="入力画像のパス")
    parser.add_argument("--output", help="保存先 npy ファイル（任意）")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        raise FileNotFoundError(f"画像が見つかりません: {image_path}")

    logits = get_raw_output(str(image_path))
    print("logits shape:", logits.shape)
    print("logits:", logits)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(output_path, logits)
        print(f"saved to {output_path}")


if __name__ == "__main__":
    main()