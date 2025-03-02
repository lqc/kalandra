import asyncio
import logging
import sys

from kalandra.cli import main

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    result = asyncio.run(main(sys.argv))
    sys.exit(result)
