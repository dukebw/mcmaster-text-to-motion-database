import os
import sys
import json

def train_from_config(argv=None):
    """Calls `train.py` using a set of parameters taken from an input JSON
    config file.

    CUDA_VISIBLE_DEVICES is set using the second parameter passed to this
    Python script, or set to nothing (i.e. use CPU) if no second parameter is
    passed.

    I.e. for using GPU 0:
        python3 -m train_from_config.py config.json 0

    Or specifying _no_ GPU (i.e. use CPU):
        python3 -m train_from_config.py config.json
    """
    assert len(argv) >= 2, "Usage: python3 -m train [config.json] [GPU_DEVICES]"

    cuda_visible_devices = 'CUDA_VISIBLE_DEVICES='
    if len(argv) > 2:
        cuda_visible_devices += argv[2]

    with open(argv[1]) as config_file:
            config = json.load(config_file)

    config_string = ""
    for option in config:
        config_string += ' --' + option + ' ' + str(config[option])

    os.system(cuda_visible_devices + ' python3 -m train' + config_string)


if __name__ == "__main__":
    train_from_config(sys.argv)
