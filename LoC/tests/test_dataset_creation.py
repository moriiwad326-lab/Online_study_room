from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import dataset_creation


def test_build_pipeline_config(tmp_path):
    config = dataset_creation.build_pipeline_config(
        input_path=tmp_path / 'video.mp4',
        output_root=tmp_path / 'output',
        rate_seconds=5,
        window=4,
        model='dummy-model',
        simulate=True,
    )

    assert config['video_input'] == str(tmp_path / 'video.mp4')
    assert config['frame_output_dir'] == str(tmp_path / 'output' / 'video')
    assert config['labels_csv'] == str(tmp_path / 'output' / 'video' / 'labels.csv')
    assert config['rate_seconds'] == 5
    assert config['window'] == 4
    assert config['model'] == 'dummy-model'
    assert config['simulate'] is True
