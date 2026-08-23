import sqlite3
import logging
from config import CentralConfig

logger = logging.getLogger("RoomSelector")

class RoomSelector:
    def __init__(self, config: CentralConfig = None):
        self.config = config or CentralConfig()

    def _conn(self, db_path):
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def is_room_available(self, conn, room_id, req_start_date, req_end_date, req_start_time, req_end_time, req_days, exclude_schedule_id=None):
        if not req_start_date or not req_end_date or not req_start_time or not req_end_time:
            return True
            
        query = 'SELECT id, start_time, end_time, days FROM course_schedule WHERE room_id = ? AND status != "cancelled"'
        params = [room_id]
        if exclude_schedule_id:
            query += ' AND id != ?'
            params.append(exclude_schedule_id)
            
        existing_schedules = conn.execute(query, params).fetchall()
        req_days_set = set(d.strip().lower() for d in (req_days or '').split())

        for row in existing_schedules:
            ex_start_dt = str(row['start_time'])
            ex_end_dt = str(row['end_time'])
            
            if ' ' in ex_start_dt:
                ex_start_date, ex_start_time_val = ex_start_dt.split(' ')[0], ex_start_dt.split(' ')[1][:5]
            else:
                ex_start_date, ex_start_time_val = ex_start_dt, "00:00"

            if ' ' in ex_end_dt:
                ex_end_date, ex_end_time_val = ex_end_dt.split(' ')[0], ex_end_dt.split(' ')[1][:5]
            else:
                ex_end_date, ex_end_time_val = ex_end_dt, "23:59"

            ex_days = str(row['days'] or '')
            
            # Check date overlap
            if req_start_date <= ex_end_date and req_end_date >= ex_start_date:
                # Check time overlap
                if req_start_time < ex_end_time_val and req_end_time > ex_start_time_val:
                    ex_days_set = set(d.strip().lower() for d in ex_days.split())
                    if not req_days_set or not ex_days_set or not req_days_set.isdisjoint(ex_days_set):
                        # Check cancelled dates
                        exceptions = conn.execute(
                            'SELECT exception_date FROM schedule_exceptions WHERE schedule_id = ? AND status = "cancelled"',
                            (row['id'],)
                        ).fetchall()
                        cancelled_dates = set(r['exception_date'] for r in exceptions)

                        import datetime
                        try:
                            start_check = max(req_start_date, ex_start_date)
                            end_check = min(req_end_date, ex_end_date)
                            sd = datetime.datetime.strptime(start_check, '%Y-%m-%d')
                            ed = datetime.datetime.strptime(end_check, '%Y-%m-%d')
                        except Exception:
                            return False

                        has_active_conflict = False
                        curr = sd
                        while curr <= ed:
                            curr_str = curr.strftime('%Y-%m-%d')
                            if row['days']:
                                day_name = curr.strftime('%a').lower()
                                is_on_day = any(d.strip().lower().startswith(day_name) for d in str(row['days']).split())
                            else:
                                is_on_day = True

                            if is_on_day:
                                if curr_str not in cancelled_dates:
                                    has_active_conflict = True
                                    break
                            curr += datetime.timedelta(days=1)

                        if has_active_conflict:
                            return False
        return True

    def find_best_room(self, student_count, requirements, req_start_date=None, req_end_date=None, req_start_time=None, req_end_time=None, req_days=None, exclude_schedule_id=None):
        allowed = {"has_projector", "has_pcs", "has_ventilation"}
        clauses = [f"has_{r[4:]}=1" if r.startswith("has_") else f"{r}=1" for r in requirements if r in allowed or f"has_{r}" in allowed or r.replace("has_", "") in allowed]
        
        clean_clauses = []
        for cl in requirements:
            if cl == "has_projector" or cl == "projector":
                clean_clauses.append("has_projector=1")
            elif cl == "has_pcs" or cl == "pcs":
                clean_clauses.append("has_pcs=1")
            elif cl == "has_ventilation" or cl == "ventilation":
                clean_clauses.append("has_ventilation=1")

        q = "SELECT * FROM classroom_metadata WHERE capacity >= ?"
        params = [student_count]
        if clean_clauses:
            q += " AND " + " AND ".join(clean_clauses)

        # Fetch classrooms matching metadata
        with self._conn(self.config.db_classroom) as classroom_conn:
            rooms = [dict(r) for r in classroom_conn.execute(q, params).fetchall()]
        
        if not rooms:
            return None

        # Fetch manual command count from control_logs.db
        with self._conn(self.config.db_control_logs) as logs_conn:
            for r in rooms:
                row = logs_conn.execute(
                    "SELECT COUNT(*) as cnt FROM control_logs WHERE room_id = ? AND timestamp > datetime('now', '-30 days')",
                    (r['room_id'],)
                ).fetchone()
                r['manual_command_count'] = row['cnt'] if row else 0

        # Filter out rooms with schedule conflicts in schedule.db
        available_rooms = []
        with self._conn(self.config.db_schedule) as schedule_conn:
            for r in rooms:
                if self.is_room_available(schedule_conn, r['room_id'], req_start_date, req_end_date, req_start_time, req_end_time, req_days, exclude_schedule_id):
                    available_rooms.append(r)
                    
        if not available_rooms:
            return None

        # Scoring
        return max(available_rooms,
                   key=lambda r: (
                       (r['avg_efficiency_score'] or 50.0) -
                       ((r['capacity'] - student_count) * 0.1) - 
                       ((r['thermal_loss_rate'] or 0.5) * 15.0) -
                       ((r.get('manual_command_count', 0) / 5.0))
                   ))