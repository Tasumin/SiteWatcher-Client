import sqlite3, json, os, time

class Storage:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""create table if not exists queue (
            id integer primary key autoincrement,
            payload text not null,
            created_at integer not null
        )""")
        self.db.commit()

    def enqueue(self, payload):
        self.db.execute("insert into queue(payload, created_at) values (?,?)", (json.dumps(payload), int(time.time())))
        self.db.commit()

    def batches(self, limit=20):
        rows = self.db.execute("select id,payload from queue order by id limit ?", (limit,)).fetchall()
        return [(r[0], json.loads(r[1])) for r in rows]

    def delete(self, ids):
        if not ids: return
        self.db.executemany("delete from queue where id=?", [(i,) for i in ids])
        self.db.commit()
