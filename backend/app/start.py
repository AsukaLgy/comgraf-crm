import subprocess
import sys
import uvicorn

subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
uvicorn.run('app.main:app', host='0.0.0.0', port=8000, workers=2)
