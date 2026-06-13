import cv2
import mediapipe as mp

# landmarkの繋がり表示用
landmark_line_ids = [ 
    (0, 1), (1, 5), (5, 9), (9, 13), (13, 17), (17, 0),  # 掌
    (1, 2), (2, 3), (3, 4),         # 親指
    (5, 6), (6, 7), (7, 8),         # 人差し指
    (9, 10), (10, 11), (11, 12),    # 中指
    (13, 14), (14, 15), (15, 16),   # 薬指
    (17, 18), (18, 19), (19, 20),   # 小指
]

import importlib

HandsClass = None
# Try the common attribute first
try:
    HandsClass = mp.solutions.hands.Hands
except Exception:
    HandsClass = None

if HandsClass is None:
    # Try several known module paths for mediapipe's solutions.hands
    candidates = (
        'mediapipe.python.solutions.hands',
        'mediapipe.python.solutions',
        'mediapipe.solutions.hands',
        'mediapipe.solutions',
    )
    for modname in candidates:
        try:
            m = importlib.import_module(modname)
        except Exception:
            continue
        # Module may directly expose Hands
        if hasattr(m, 'Hands'):
            HandsClass = m.Hands
            break
        # Module may expose a `hands` submodule or attribute
        if hasattr(m, 'hands'):
            h = getattr(m, 'hands')
            if hasattr(h, 'Hands'):
                HandsClass = h.Hands
                break

if HandsClass is None:
    import sys
    import pkgutil
    import os

    details = []
    details.append(f'Python: {sys.version.replace(os.linesep, " ")}')
    details.append(f"mediapipe module file: {getattr(mp, '__file__', None)}")
    if hasattr(mp, '__path__'):
        try:
            names = [m.name for m in pkgutil.iter_modules(mp.__path__)]
        except Exception:
            names = []
        details.append('mediapipe package contents: ' + ', '.join(names))

    msg = (
        'Could not locate mediapipe solutions.hands.Hands.\n'
        'This usually means the installed `mediapipe` package is missing the `solutions` subpackage or is incompatible with your Python version.\n'
        'Details:\n' + '\n'.join(details) + '\n\n'
        'Suggested fixes:\n'
        '- Use a supported Python version (e.g. 3.8/3.9/3.10/3.11) and install mediapipe with `pip install mediapipe`.\n'
        '- If you must keep Python 3.14, install mediapipe from source or use a compatible wheel (may be difficult).\n'
        '- Create a virtualenv with a supported Python and run the script there.\n'
        '\nExample (PowerShell):\n'
        'py -3.10 -m venv .venv\n'
        '.\.venv\Scripts\Activate.ps1\n'
        'pip install --upgrade pip\n'
        'pip install mediapipe opencv-python\n'
    )
    raise ImportError(msg)

hands = HandsClass(
    max_num_hands=2,                # 最大検出数
    min_detection_confidence=0.7,   # 検出信頼度
    min_tracking_confidence=0.7     # 追跡信頼度
)

cap = cv2.VideoCapture(0)   # カメラのID指定
if cap.isOpened():
    while True:
        # カメラから画像取得
        success, img = cap.read()
        if not success:
            continue
        img = cv2.flip(img, 1)          # 画像を左右反転
        img_h, img_w, _ = img.shape     # サイズ取得

        # 検出処理の実行
        results = hands.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if results.multi_hand_landmarks:
            # 検出した手の数分繰り返し
            for h_id, hand_landmarks in enumerate(results.multi_hand_landmarks):

                # landmarkの繋がりをlineで表示
                for line_id in landmark_line_ids:
                    # 1点目座標取得
                    lm = hand_landmarks.landmark[line_id[0]]
                    lm_pos1 = (int(lm.x * img_w), int(lm.y * img_h))
                    # 2点目座標取得
                    lm = hand_landmarks.landmark[line_id[1]]
                    lm_pos2 = (int(lm.x * img_w), int(lm.y * img_h))
                    # line描画
                    cv2.line(img, lm_pos1, lm_pos2, (128, 0, 0), 1)

                # landmarkをcircleで表示
                z_list = [lm.z for lm in hand_landmarks.landmark]
                z_min = min(z_list)
                z_max = max(z_list)
                for lm in hand_landmarks.landmark:
                    lm_pos = (int(lm.x * img_w), int(lm.y * img_h))
                    lm_z = int((lm.z - z_min) / (z_max - z_min) * 255)
                    cv2.circle(img, lm_pos, 3, (255, lm_z, lm_z), -1)

                # 検出情報をテキスト出力
                # - テキスト情報を作成
                hand_texts = []
                for c_id, hand_class in enumerate(results.multi_handedness[h_id].classification):
                    hand_texts.append("#%d-%d" % (h_id, c_id)) 
                    hand_texts.append("- Index:%d" % (hand_class.index))
                    hand_texts.append("- Label:%s" % (hand_class.label))
                    hand_texts.append("- Score:%3.2f" % (hand_class.score * 100))
                # - テキスト表示に必要な座標など準備
                lm = hand_landmarks.landmark[0]
                lm_x = int(lm.x * img_w) - 50
                lm_y = int(lm.y * img_h) - 10
                lm_c = (64, 0, 0)
                font = cv2.FONT_HERSHEY_SIMPLEX
                # - テキスト出力
                for cnt, text in enumerate(hand_texts):
                    cv2.putText(img, text, (lm_x, lm_y + 10 * cnt), font, 0.3, lm_c, 1)

        # 画像の表示
        cv2.imshow("MediaPipe Hands", img)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == ord('Q') or key == 0x1b:
            break

cap.release()
