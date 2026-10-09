# Richseed Private School — Admin Backend

A working backend for the **Admin Portal** only (Teacher/Parent/Student portals come next).
Built with FastAPI + SQLite. Every endpoint below has been tested and works.

## What's included

- Admin PIN login (default PIN: `1234`, stored in the database, changeable)
- Overview stats (teacher/student counts, who's present today)
- Teachers: enroll (auto-generates a 4-digit PIN), list, delete, CSV export, 50-teacher cap
- Teacher profile drill-down (see a teacher's students)
- Finance: fee structure (add line items), student payment status
- Results access control: global lock/unlock + per-student approve/pending
- Location: geofence (latitude/longitude/radius) for attendance check-in
- Messages: admin → teacher, with thread history

## 1. Run it on your own computer

You need Python 3.10+ installed.

```bash
cd richseed-backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API will start at `http://localhost:8000`. Open `http://localhost:8000/docs` in
your browser — FastAPI auto-generates an interactive page there where you can click
"Try it out" on every endpoint without writing any code. Use that to explore before
wiring up the frontend.

A file called `richseed.db` will be created automatically on first run, pre-filled
with the 3 demo teachers, default fee structure, PIN `1234`, and the default geofence.

## 2. Deploy it live (so your real website can reach it)

Once it works locally, put it online. Two easy free options:

**Render.com** (recommended, simplest):
1. Push this folder to a GitHub repo.
2. On Render.com → New → Web Service → connect that repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Deploy. Render gives you a live URL like `https://richseed-api.onrender.com`.

**Railway.app** works the same way — connect the repo, it detects Python
automatically, same start command.

Once deployed, your frontend calls that URL instead of `localhost`.

## 3. Connect the frontend

In the HTML file, every place that currently uses the in-memory demo arrays
(`teachers`, `students`, `feeStructure`, etc.) needs to be replaced with a `fetch()`
call to these endpoints instead. Example for admin login:

```javascript
async function checkAdminPin(pin) {
  const res = await fetch('https://your-deployed-url.com/api/admin/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ pin })
  });
  if (!res.ok) { alert('Incorrect PIN'); return false; }
  return true;
}
```

I'll help wire up each section of the frontend once this is deployed — send me the
live URL and we'll go tab by tab (Overview, Teachers, Finance, Results, Location,
Messages).

## Endpoint reference

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/admin/login` | Check admin PIN — body: `{"pin": "1234"}` |
| GET | `/api/admin/overview` | Dashboard stats |
| GET | `/api/admin/teachers` | List all teachers |
| POST | `/api/admin/teachers` | Enroll teacher — body: `{"name","subject"}` |
| DELETE | `/api/admin/teachers/{id}` | Remove a teacher |
| GET | `/api/admin/teachers/{id}/profile` | Teacher + their students |
| GET | `/api/admin/teachers/export.csv` | Download teacher codes as CSV |
| GET | `/api/admin/students` | List all students |
| GET | `/api/admin/fees` | Get fee structure |
| POST | `/api/admin/fees` | Add a fee line item — body: `{"name","amount"}` |
| GET | `/api/admin/payments` | Payment status per student |
| GET | `/api/admin/results/lock` | Get global results lock state |
| POST | `/api/admin/results/lock?locked=true` | Set global lock |
| POST | `/api/admin/students/{id}/result-access` | Approve/pending one student — body: `{"result_access":"approved"}` |
| GET | `/api/admin/geofence` | Get school location/radius |
| POST | `/api/admin/geofence` | Set it — body: `{"latitude","longitude","radius_m"}` |
| GET | `/api/admin/messages` | List admin↔teacher messages |
| POST | `/api/admin/messages` | Send to a teacher — body: `{"teacher_id","body","subject"}` |

## Notes

- Currently every teacher/student is visible to any request — there's no per-teacher
  login token yet. That's fine for the Admin portal (only one PIN gate), but the
  Teacher and Parent portals (next step) will need proper per-user auth tokens so a
  teacher can only see their own class. We'll add that when we build those.
- Switching from SQLite to PostgreSQL later only requires changing one line in
  `app/database.py` — nothing else in the code needs to change.
