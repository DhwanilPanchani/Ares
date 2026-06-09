"""
Tool registry — master list of all worker agent tools.

The worker agent binds this list to the LLM via:
    llm.bind_tools(ALL_TOOLS)

Add tools here to make them available to every worker node.
The order determines presentation order in the model's tool schema.
"""

from backend.tools.code_exec import run_python
from backend.tools.file_ops import read_file, write_file
from backend.tools.http_get import http_get
from backend.tools.web_search import web_search

ALL_TOOLS = [
    web_search,
    read_file,
    write_file,
    run_python,
    http_get,
]
