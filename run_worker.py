import sys
from pathlib import Path

# Гарантируем, что питон видит папку app
sys.path.append(str(Path(__file__).parent))

from app.runtime.worker_entrypoint import main

if __name__ == "__main__":
    main()
