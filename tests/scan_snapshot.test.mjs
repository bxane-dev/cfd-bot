import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const source = fs.readFileSync(new URL("../app/main.py", import.meta.url), "utf8");

test("run_once reuses the account position snapshot for read-only checks", () => {
  assert.match(
    source,
    /def open_index_count\(broker, markets: list\[Market\], positions=None\) -> int:/,
  );
  assert.match(
    source,
    /def note_closed_positions\(broker, state: dict, positions=None\) -> None:/,
  );
  assert.match(source, /positions = list\(acct\.positions or \[\]\)/);
  assert.match(source, /idx_open = open_index_count\(broker, markets, positions\)/);
  assert.match(source, /note_closed_positions\(broker, state, positions\)/);
});
