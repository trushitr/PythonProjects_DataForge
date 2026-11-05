import os
import tempfile

# Runs before any test module imports the app, so every test file uses a
# throwaway database and a known signing secret regardless of collection order.
os.environ["DATAFORGE_DATA_DIR"] = tempfile.mkdtemp(prefix="dataforge-test-")
os.environ["DATAFORGE_JWT_SECRET"] = "test-secret-" + "x" * 40
