import argparse
import subprocess
import time
import sys
import os

def test00():
    # res = subprocess.run(['ls', '-a', '-l'], capture_output=True, text=True, check=True)
    res = subprocess.run(['ls', '-a -l'], capture_output=True, text=True, check=True)
    print(f"res: {res}")

if __name__ == "__main__":
    test00()