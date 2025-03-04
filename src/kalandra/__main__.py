import asyncio
import logging
import sys

from kalandra.cli import main


def run():
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s] %(levelname)-8s | %(filename)s:%(lineno)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    result = asyncio.run(main(sys.argv[1:]))
    sys.exit(result)


if __name__ == "__main__":
    run()
