# document-processing-mcp

Document LangGraph pipeline as FastMCP (`:8001`). Copy this folder to its own git.

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt   # pytest
cp .env.example .env
./run.sh
# tests: pytest

# or: python run.py --transport http --host 127.0.0.1 --port 8001
```
