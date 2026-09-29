#!/bin/bash
# «Одно окно»: двойной щелчок на Mac — откроется http://127.0.0.1:8765 в браузере.
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
python3 -m lot_analyzer.app
