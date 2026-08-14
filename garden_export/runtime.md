# runtime

python 3.13 is installed.

third-party python packages:

- openai
- numpy
- sympy
- networkx
- rich
- pyyaml
- beautifulsoup4
- markdownify
- fastapi
- uvicorn
- websockets
- jinja2
- pyzmq
- aiosqlite
- psutil
- watchfiles
- simpy
- jsonschema
- pytest
- hypothesis
- ruff
- pillow
- matplotlib
- pygments
- lark
- python-chess
- pycryptodome
- sortedcontainers
- more-itertools
- python-dateutil
- msgpack

git and posix shell facilities are installed.

the container is limited to 2 cpu, 5 gib of memory, and 256 processes. the working tree is limited to 4 gib.

the container has no network interface. limited web retrieval is available through /diode, which accepts a closed command vocabulary.

the model endpoint used by this environment is a unix domain socket. it accepts connections from any process in the container.

filesystem locations can differ in ownership, mutability, and lifecycle.
