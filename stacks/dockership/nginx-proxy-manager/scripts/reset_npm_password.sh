#!/bin/bash
set -euo pipefail

CONTAINER="nginx-proxy-manager"
DB="/data/database.sqlite"
AUTH_ID=1

echo "=========================================="
echo " Nginx Proxy Manager Password Reset"
echo "=========================================="
echo

# Check container
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "ERROR: Container '$CONTAINER' not found."
    exit 1
fi

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" | grep -q true; then
    echo "ERROR: Container '$CONTAINER' is not running."
    exit 1
fi

# Get email
EMAIL=$(docker exec "$CONTAINER" node -e '
const sqlite3=require("/app/node_modules/sqlite3");
const db=new sqlite3.Database("/data/database.sqlite");
db.get(
  "SELECT email FROM user WHERE id=1",
  (err,row) => {
    if(err) throw err;
    console.log(row ? row.email : "");
    db.close();
  }
)
')

echo "Account: $EMAIL"
echo

# Get new password
read -rsp "Enter NEW password: " NPM_PASS
echo
read -rsp "Confirm NEW password: " NPM_PASS_CONFIRM
echo

if [[ "$NPM_PASS" != "$NPM_PASS_CONFIRM" ]]; then
    echo "ERROR: Passwords do not match."
    exit 1
fi

if [[ -z "$NPM_PASS" ]]; then
    echo "ERROR: Password cannot be empty."
    exit 1
fi

echo
echo "Generating bcrypt hash..."

# Generate bcrypt hash inside container.
# Password is passed through environment variable, avoiding shell expansion of $.
HASH=$(docker exec \
    -e NPM_PASS="$NPM_PASS" \
    "$CONTAINER" \
    node -e '
const bcrypt=require("/app/node_modules/bcrypt");
process.stdout.write(bcrypt.hashSync(process.env.NPM_PASS, 10));
')

# Validate hash
if [[ ${#HASH} -ne 60 ]]; then
    echo "ERROR: Generated bcrypt hash has unexpected length: ${#HASH}"
    exit 1
fi

if [[ "$HASH" != \$2b\$10\$* ]]; then
    echo "ERROR: Generated hash does not look like bcrypt."
    exit 1
fi

echo "Bcrypt hash generated successfully."

# Update database
echo "Updating NPM authentication record..."

docker exec \
    -e NEW_HASH="$HASH" \
    "$CONTAINER" \
    node -e '
const sqlite3=require("/app/node_modules/sqlite3");
const db=new sqlite3.Database("/data/database.sqlite");

db.run(
  "UPDATE auth SET secret = ?, modified_on = datetime(\"now\") WHERE id = 1",
  [process.env.NEW_HASH],
  function(err) {
    if(err) throw err;
    console.log("Updated rows:", this.changes);

    if(this.changes !== 1) {
      process.exitCode = 1;
    }

    db.close();
  }
)
'

# Verify stored hash
echo
echo "Verifying database..."

STORED_HASH=$(docker exec "$CONTAINER" node -e '
const sqlite3=require("/app/node_modules/sqlite3");
const db=new sqlite3.Database("/data/database.sqlite");

db.get(
  "SELECT secret FROM auth WHERE id=1",
  (err,row) => {
    if(err) throw err;
    process.stdout.write(row ? row.secret : "");
    db.close();
  }
)
')

if [[ ${#STORED_HASH} -ne 60 ]]; then
    echo "ERROR: Stored hash length is ${#STORED_HASH}, expected 60."
    exit 1
fi

echo "Stored bcrypt hash verified."

# Verify password against database
echo "Testing new password..."

MATCH=$(docker exec \
    -e NPM_PASS="$NPM_PASS" \
    "$CONTAINER" \
    node -e '
const bcrypt=require("/app/node_modules/bcrypt");
const sqlite3=require("/app/node_modules/sqlite3");

const db=new sqlite3.Database("/data/database.sqlite");

db.get(
  "SELECT secret FROM auth WHERE id=1",
  (err,row) => {
    if(err) throw err;

    bcrypt.compare(
      process.env.NPM_PASS,
      row.secret,
      (err,result) => {
        if(err) throw err;
        process.stdout.write(result ? "true" : "false");
        db.close();
      }
    );
  }
)
')

if [[ "$MATCH" != "true" ]]; then
    echo "ERROR: Password verification FAILED."
    echo "NPM was NOT restarted."
    exit 1
fi

echo "Password verification: SUCCESS"

# Restart NPM
echo
echo "Restarting Nginx Proxy Manager..."

docker restart "$CONTAINER"

echo
echo "=========================================="
echo " Password reset completed successfully"
echo "=========================================="
echo
echo "Login email: $EMAIL"
echo "Use the NEW password you entered above."
echo