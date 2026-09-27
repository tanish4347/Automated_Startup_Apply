import sys

with open("src/api/main.py", "r") as f:
    content = f.read()

import_statement = "from src.api.profile import router as profile_router\napp.include_router(profile_router)\n"

if "profile_router" not in content:
    content = content + "\n" + import_statement

with open("src/api/main.py", "w") as f:
    f.write(content)
