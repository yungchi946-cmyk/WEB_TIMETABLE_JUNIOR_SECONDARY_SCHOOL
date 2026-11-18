"""
Timetable Engine for SchoolSync – WITH SUBJECT REPETITION CONSTRAINT
--------------------------------
All rules:
- Teachers present every day (days field ignored).
- Maths & English exactly 4 times per week per arm.
- No double‑booking.
- Class arms are configurable per level (passed in; defaults to A/B/C).
- NEW: Same subject cannot appear twice in a day between 8am-2pm,
  but may repeat after the short break (2:15pm onward).
"""

import random

DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

LEVEL_ARMS = {
    'JSS1': ['JSS1A', 'JSS1B', 'JSS1C'],
    'JSS2': ['JSS2A', 'JSS2B', 'JSS2C'],
    'JSS3': ['JSS3A', 'JSS3B', 'JSS3C'],
}

MANDATORY_SUBJECTS = {
    'Mathematics': 4,
    'English': 4,
}

def fmt_time(h, m):
    suffix = 'AM' if h < 12 else 'PM'
    hh = h if h <= 12 else h - 12
    if hh == 0: hh = 12
    return f"{hh}:{m:02d} {suffix}"

def build_day_schedule(day):
    """Returns list of slots (periods & breaks) for the given day.
       Each slot dict includes 'start_min' and 'end_min' (minutes since midnight)."""
    schedule = []
    def t(h, m): return h * 60 + m
    def fmt(m):
        hh, mm = divmod(m, 60)
        return fmt_time(hh, mm)

    BREAK1_START = t(11,35); BREAK1_END = t(12,0)
    BREAK2_START = t(14,0); BREAK2_END = t(14,15)
    END_MTTH = t(16,0); END_FRI = t(14,0)

    is_friday = (day == 'Friday')
    end_time = END_FRI if is_friday else END_MTTH
    cursor = t(8,0)
    period_num = 1

    while cursor < end_time:
        if cursor == BREAK1_START:
            schedule.append({
                'slot_num': None, 'kind': 'break', 'label': 'Break Time',
                'start': fmt(BREAK1_START), 'end': fmt(BREAK1_END),
                'start_min': BREAK1_START, 'end_min': BREAK1_END
            })
            cursor = BREAK1_END
            continue
        if not is_friday and cursor == BREAK2_START:
            schedule.append({
                'slot_num': None, 'kind': 'break', 'label': 'Short Break',
                'start': fmt(BREAK2_START), 'end': fmt(BREAK2_END),
                'start_min': BREAK2_START, 'end_min': BREAK2_END
            })
            cursor = BREAK2_END
            continue

        period_end = cursor + 40
        actual_end = min(period_end, end_time)
        # handle breaks intersecting
        if cursor < BREAK1_START < period_end:
            actual_end = BREAK1_START
        elif not is_friday and cursor < BREAK2_START < period_end:
            actual_end = BREAK2_START

        schedule.append({
            'slot_num': period_num, 'kind': 'period', 'label': f'Period {period_num}',
            'start': fmt(cursor), 'end': fmt(actual_end),
            'start_min': cursor, 'end_min': actual_end
        })
        cursor = actual_end
        period_num += 1

    schedule.append({
        'slot_num': None, 'kind': 'close', 'label': 'School Closes',
        'start': fmt(end_time), 'end': '', 'start_min': end_time, 'end_min': end_time
    })
    return schedule

class TimetableEngine:
    def __init__(self, teachers, arms=None):
        """arms: {'JSS1': ['A','B'], 'JSS2': ['A','B','C','D'], ...} (arm suffixes).
           Defaults to LEVEL_ARMS when not supplied."""
        self.teachers = teachers
        if arms is None:
            self.arms = {lvl: list(a) for lvl, a in LEVEL_ARMS.items()}
        else:
            self.arms = {lvl: [f"{lvl}{n}" for n in names] for lvl, names in arms.items()}

    def generate(self):
        print("DEBUG: Starting generation with", len(self.teachers), "teachers", flush=True)
        level_pool = {lvl: [] for lvl in LEVEL_ARMS}
        for t in self.teachers:
            name = t['name']
            classes = t.get('classes', [])
            subjects = t.get('subjects', [])
            print(f"DEBUG: Teacher {name} -> classes={classes}, subjects={subjects}", flush=True)
            for level in classes:
                if level not in level_pool:
                    print(f"WARNING: level {level} not in LEVEL_ARMS, skipping", flush=True)
                    continue
                for subj in subjects:
                    level_pool[level].append({'subject': subj, 'teacher': name, 'days': DAYS})
        print("DEBUG: level_pool contents:", {k: len(v) for k, v in level_pool.items()}, flush=True)

        active_arms = []
        arm_level = {}
        for level, arms in self.arms.items():
            if level in level_pool and level_pool[level]:
                for a in arms:
                    active_arms.append(a)
                    arm_level[a] = level
        print("DEBUG: active_arms =", active_arms, flush=True)

        if not active_arms:
            print("ERROR: No active arms! Check teacher class selections.", flush=True)
            return {}

        teacher_booked = {}
        result = {}

        for arm in active_arms:
            level = arm_level[arm]
            pool = level_pool[level]
            print(f"DEBUG: Processing arm {arm} (level {level}) with {len(pool)} subject-teacher entries", flush=True)

            # Per-arm mandatory count
            pending_mandatory = {}
            for subj, count in MANDATORY_SUBJECTS.items():
                if any(p['subject'] == subj for p in pool):
                    pending_mandatory[subj] = count
                else:
                    print(f"WARNING: No teacher found for {subj} in {arm}, mandatory will be skipped", flush=True)
                    pending_mandatory[subj] = 0

            # Build all period slots (day + slot info)
            all_slots = []
            for day in DAYS:
                for slot in build_day_schedule(day):
                    if slot['kind'] == 'period':
                        all_slots.append((day, slot))

            # Track subjects already used before 2pm on each day
            subjects_before_2pm_per_day = {day: set() for day in DAYS}

            # Helper to check if a subject can be placed in a given slot
            def can_place_subject(day, slot, subject):
                # slot is a dict with start_min, end_min
                # if slot ends at or before 14:00 -> before short break
                if slot['end_min'] <= 14 * 60:
                    return subject not in subjects_before_2pm_per_day[day]
                # after short break -> always allowed (may repeat)
                return True

            # Mark subject as placed (only for pre‑2pm slots)
            def mark_subject_placed(day, slot, subject):
                if slot['end_min'] <= 14 * 60:
                    subjects_before_2pm_per_day[day].add(subject)

            # Helper to find a teacher for a subject in a given period
            def find_teacher(subj, day, period_num):
                candidates = [p['teacher'] for p in pool if p['subject'] == subj
                              and day in p['days']
                              and p['teacher'] not in teacher_booked.get((day, period_num), set())]
                return random.choice(candidates) if candidates else None

            # Plan dict: (day, slot_num) -> assignment dict
            plan = {}
            for day, slot in all_slots:
                plan[(day, slot['slot_num'])] = None

            # ---- Mandatory subject placement ----
            keys = list(plan.keys())
            random.shuffle(keys)

            # First pass: place mandatory subjects where possible
            for day, pnum in keys:
                if plan[(day, pnum)] is not None:
                    continue

                # Find the slot details for this (day, pnum)
                slot_info = next(slot for d, slot in all_slots if d == day and slot['slot_num'] == pnum)

                # Available mandatory subjects (still needed and allowed in this slot)
                available_mandatory = [
                    s for s, c in pending_mandatory.items() if c > 0
                    and can_place_subject(day, slot_info, s)
                ]
                if not available_mandatory:
                    continue

                subj = random.choice(available_mandatory)
                teacher = find_teacher(subj, day, pnum)
                if teacher:
                    plan[(day, pnum)] = {'subject': subj, 'teacher': teacher}
                    teacher_booked.setdefault((day, pnum), set()).add(teacher)
                    pending_mandatory[subj] -= 1
                    mark_subject_placed(day, slot_info, subj)
                    print(f"DEBUG: {arm}: {day} P{pnum} -> mandatory {subj} ({teacher})", flush=True)
                else:
                    print(f"DEBUG: {arm}: {day} P{pnum} -> no free teacher for mandatory {subj}", flush=True)

            # Second pass for remaining mandatory subjects
            for day, pnum in keys:
                if plan[(day, pnum)] is not None:
                    continue
                slot_info = next(slot for d, slot in all_slots if d == day and slot['slot_num'] == pnum)
                available_mandatory = [
                    s for s, c in pending_mandatory.items() if c > 0
                    and can_place_subject(day, slot_info, s)
                ]
                if not available_mandatory:
                    continue
                subj = random.choice(available_mandatory)
                teacher = find_teacher(subj, day, pnum)
                if teacher:
                    plan[(day, pnum)] = {'subject': subj, 'teacher': teacher}
                    teacher_booked.setdefault((day, pnum), set()).add(teacher)
                    pending_mandatory[subj] -= 1
                    mark_subject_placed(day, slot_info, subj)

            # ---- Fill remaining slots with optional subjects ----
            for day, pnum in keys:
                if plan[(day, pnum)] is not None:
                    continue
                slot_info = next(slot for d, slot in all_slots if d == day and slot['slot_num'] == pnum)

                # Build list of optional subjects (all subjects not mandatory, or mandatory with count=0)
                optional_pool = [p for p in pool if p['subject'] not in MANDATORY_SUBJECTS
                                 or pending_mandatory.get(p['subject'], 0) == 0]
                if not optional_pool:
                    optional_pool = pool[:]

                random.shuffle(optional_pool)
                assigned = False
                for cand in optional_pool:
                    # Check teacher availability
                    if cand['teacher'] in teacher_booked.get((day, pnum), set()):
                        continue
                    # Check subject repetition constraint
                    if not can_place_subject(day, slot_info, cand['subject']):
                        continue
                    # Valid assignment
                    plan[(day, pnum)] = {'subject': cand['subject'], 'teacher': cand['teacher']}
                    teacher_booked.setdefault((day, pnum), set()).add(cand['teacher'])
                    mark_subject_placed(day, slot_info, cand['subject'])
                    assigned = True
                    break
                if not assigned:
                    plan[(day, pnum)] = {'subject': 'Free Period', 'teacher': ''}

            # ---- Build final timetable for this arm ----
            arm_tt = {}
            for day in DAYS:
                template = build_day_schedule(day)
                filled = []
                for slot in template:
                    entry = dict(slot)
                    if slot['kind'] == 'period':
                        pnum = slot['slot_num']
                        if (day, pnum) in plan and plan[(day, pnum)] is not None:
                            entry['subject'] = plan[(day, pnum)]['subject']
                            entry['teacher'] = plan[(day, pnum)]['teacher']
                        else:
                            entry['subject'] = 'Free Period'
                            entry['teacher'] = ''
                    filled.append(entry)
                arm_tt[day] = filled
            result[arm] = arm_tt

        print("DEBUG: Generation complete. Arms generated:", list(result.keys()), flush=True)
        return result