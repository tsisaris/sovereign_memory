import re

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "r") as f:
    content = f.read()

# Since `self.db` might be ':memory:', the tables created in `setup_schema` with a temporary connection 
# will be lost as soon as the connection closes.
# Let's keep a permanent connection in the driver.

sub = """
    def __init__(
        self,
        db: str = ':memory:',
    ):
        super().__init__()
        self.db = db
        self.conn = duckdb.connect(self.db)
        self.setup_schema()"""

content = re.sub(r"def __init__\([\s\S]*?self\.setup_schema\(\)", sub, content)

sub2 = """    def setup_schema(self):
        for q in SCHEMA_QUERIES:
            self.conn.execute(q)
"""

content = re.sub(r"def setup_schema\(self\):[\s\S]*?conn\.execute\(q\)", sub2, content)

content = content.replace("with duckdb.connect(self.db) as conn:", "conn = self.conn")

with open("packages/graphiti_duckdb/src/graphiti_duckdb/duckdb_driver.py", "w") as f:
    f.write(content)
