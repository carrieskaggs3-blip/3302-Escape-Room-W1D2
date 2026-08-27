import streamlit as st
import time
import csv
import io
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="NUT 3302 | Nutrition Standards Escape Room",
    page_icon="🥗",
    layout="wide"
)

# -----------------------------
# STYLE
# -----------------------------
st.markdown("""
<style>
    .block-container {max-width: 1050px; padding-top: 2rem; padding-bottom: 3rem;}
    .room-card {
        padding: 1.2rem 1.4rem;
        border: 1px solid rgba(128,128,128,.25);
        border-radius: 16px;
        margin-bottom: 1rem;
    }
    .small-muted {opacity:.72; font-size:.92rem;}
    .letter-box {
        font-size: 2rem;
        font-weight: 700;
        text-align:center;
        border: 2px dashed rgba(128,128,128,.5);
        border-radius: 12px;
        padding: .5rem;
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------
# CONSTANTS
# -----------------------------
ROOMS = [
    "Room 1: The DRI Archive",
    "Room 2: The Protein Puzzle",
    "Room 3: The Label Lab",
    "Room 4: The AMDR Vault",
    "Room 5: The Clinical Decision Room",
    "Room 6: The Food Choice Challenge",
    "Room 7: The Final Patient",
]
LETTERS = ["B", "A", "L", "A", "N", "C", "E"]
FINAL_WORD = "BALANCE"

HINT_POINT_PENALTY = 5
HINT_TIME_PENALTY_SECONDS = 120
WRONG_ATTEMPT_PENALTY = 2
RESULTS_SHEET_NAME = "Results"
RESULT_HEADERS = [
    "submission_id", "timestamp", "mode", "student_names", "section", "team_name",
    "score", "accuracy_score", "efficiency_score", "actual_seconds", "adjusted_seconds",
    "hints", "wrong_attempts", "room1_seconds", "room2_seconds", "room3_seconds",
    "room4_seconds", "room5_seconds", "room1_attempts", "room2_attempts", "room3_attempts",
    "room4_attempts", "room5_attempts", "room1_hints", "room2_hints", "room3_hints",
    "room4_hints", "room5_hints", "final_attempts", "final_hint_used"
]

# -----------------------------
# SESSION STATE
# -----------------------------
defaults = {
    "started": False,
    "finished": False,
    "mode": "Individual",
    "names": "",
    "section": "",
    "team_name": "",
    "start_time": None,
    "finish_time": None,
    "current_room": 0,
    "room_start_times": {},
    "room_finish_times": {},
    "room_attempts": {i: 0 for i in range(7)},
    "room_hints": {i: 0 for i in range(7)},
    "room_complete": {i: False for i in range(7)},
    "letters": [],
    "final_attempts": 0,
    "final_hint_used": False,
    "final_start_time": None,
    "final_finish_time": None,
    "submission_id": "",
    "submission_saved": False,
    "submission_save_error": "",
    "faculty_authenticated": False,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

def now():
    return time.time()

def fmt_seconds(seconds):
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m} min {s:02d} sec"

def start_room(idx):
    if idx not in st.session_state.room_start_times:
        st.session_state.room_start_times[idx] = now()

def complete_room(idx):
    if not st.session_state.room_complete[idx]:
        st.session_state.room_complete[idx] = True
        st.session_state.room_finish_times[idx] = now()
        st.session_state.letters.append(LETTERS[idx])
    if idx < 6:
        st.session_state.current_room = idx + 1
        start_room(idx + 1)
    else:
        if st.session_state.final_start_time is None:
            st.session_state.final_start_time = now()
        st.session_state.current_room = 7
    st.rerun()

def register_wrong(idx):
    st.session_state.room_attempts[idx] += 1

def reveal_hint(idx):
    st.session_state.room_hints[idx] += 1

def room_elapsed(idx):
    start = st.session_state.room_start_times.get(idx)
    finish = st.session_state.room_finish_times.get(idx)
    if not start:
        return 0
    return (finish or now()) - start

def total_hint_count():
    return sum(st.session_state.room_hints.values()) + (1 if st.session_state.final_hint_used else 0)

def total_wrong_attempts():
    return sum(st.session_state.room_attempts.values()) + st.session_state.final_attempts

def actual_total_seconds():
    if not st.session_state.start_time:
        return 0
    return (st.session_state.finish_time or now()) - st.session_state.start_time

def penalty_seconds():
    return total_hint_count() * HINT_TIME_PENALTY_SECONDS

def adjusted_total_seconds():
    return actual_total_seconds() + penalty_seconds()

def calculate_score():
    # Accuracy starts at 80 points.
    accuracy = max(0, 80 - (total_wrong_attempts() * WRONG_ATTEMPT_PENALTY) - (total_hint_count() * HINT_POINT_PENALTY))

    # Efficiency contributes up to 20 points using adjusted time.
    mins = adjusted_total_seconds() / 60
    if mins <= 30:
        efficiency = 20
    elif mins <= 35:
        efficiency = 17
    elif mins <= 40:
        efficiency = 14
    elif mins <= 45:
        efficiency = 10
    elif mins <= 50:
        efficiency = 6
    else:
        efficiency = 3

    return min(100, accuracy + efficiency), accuracy, efficiency

def show_progress():
    completed = sum(1 for v in st.session_state.room_complete.values() if v)
    st.progress(completed / 7, text=f"{completed} of 7 rooms unlocked")
    if st.session_state.letters:
        st.caption("Letters collected: " + "  ".join(st.session_state.letters))

def hint_button(idx, text):
    key = f"hint_{idx}_{st.session_state.room_hints[idx]}"
    if st.button(f"Use hint ({HINT_POINT_PENALTY} points + 2 minutes)", key=key):
        reveal_hint(idx)
        st.warning(text)

def success_letter(idx):
    st.success(f"Room unlocked. Your letter is: {LETTERS[idx]}")

# -----------------------------
# SHARED RESULTS BACKEND
# -----------------------------
def eastern_now_iso():
    return datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")


def google_backend_configured():
    try:
        return "google_service_account" in st.secrets and "spreadsheet_id" in st.secrets
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def get_google_client():
    info = dict(st.secrets["google_service_account"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(credentials)


def get_results_worksheet():
    if not google_backend_configured():
        raise RuntimeError("Google Sheets storage is not configured.")
    client = get_google_client()
    spreadsheet = client.open_by_key(st.secrets["spreadsheet_id"])
    try:
        ws = spreadsheet.worksheet(RESULTS_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=RESULTS_SHEET_NAME, rows=1000, cols=len(RESULT_HEADERS) + 2)
    first_row = ws.row_values(1)
    if first_row != RESULT_HEADERS:
        if not first_row:
            ws.append_row(RESULT_HEADERS, value_input_option="RAW")
        else:
            ws.update(values=[RESULT_HEADERS], range_name="A1")
    return ws


def build_result_record():
    score, accuracy_score, efficiency_score = calculate_score()
    return [
        st.session_state.submission_id,
        eastern_now_iso(),
        st.session_state.mode,
        st.session_state.names,
        st.session_state.section,
        st.session_state.team_name,
        score,
        accuracy_score,
        efficiency_score,
        int(actual_total_seconds()),
        int(adjusted_total_seconds()),
        total_hint_count(),
        total_wrong_attempts(),
        *[int(room_elapsed(i)) for i in range(7)],
        *[st.session_state.room_attempts[i] for i in range(7)],
        *[st.session_state.room_hints[i] for i in range(7)],
        st.session_state.final_attempts,
        int(st.session_state.final_hint_used),
    ]


def save_result_to_sheet():
    if st.session_state.submission_saved:
        return True
    if not google_backend_configured():
        st.session_state.submission_save_error = "Central class results are not configured yet."
        return False
    try:
        ws = get_results_worksheet()
        # Prevent duplicate submissions if the browser reruns after completion.
        ids = ws.col_values(1)
        if st.session_state.submission_id in ids:
            st.session_state.submission_saved = True
            return True
        ws.append_row(build_result_record(), value_input_option="RAW")
        st.session_state.submission_saved = True
        st.session_state.submission_save_error = ""
        return True
    except Exception as exc:
        st.session_state.submission_save_error = str(exc)
        return False


def load_results_from_sheet():
    ws = get_results_worksheet()
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame(columns=RESULT_HEADERS)
    df = pd.DataFrame(records)
    for col in RESULT_HEADERS:
        if col not in df.columns:
            df[col] = ""
    numeric_cols = [
        "score", "accuracy_score", "efficiency_score", "actual_seconds", "adjusted_seconds",
        "hints", "wrong_attempts", "room1_seconds", "room2_seconds", "room3_seconds",
        "room4_seconds", "room5_seconds", "room1_attempts", "room2_attempts", "room3_attempts",
        "room4_attempts", "room5_attempts", "room1_hints", "room2_hints", "room3_hints",
        "room4_hints", "room5_hints", "final_attempts", "final_hint_used"
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def dashboard_from_dataframe(df):
    if df.empty:
        st.info("No completed escape-room results are available yet.")
        return

    st.subheader("Class Results")
    f1, f2, f3 = st.columns(3)
    sections = sorted([x for x in df["section"].dropna().astype(str).unique() if x])
    modes = sorted([x for x in df["mode"].dropna().astype(str).unique() if x])
    with f1:
        selected_sections = st.multiselect("Section", sections, default=sections)
    with f2:
        selected_modes = st.multiselect("Completion mode", modes, default=modes)
    with f3:
        search_name = st.text_input("Find student or team", placeholder="Type a name")

    filtered = df.copy()
    if selected_sections:
        filtered = filtered[filtered["section"].isin(selected_sections)]
    if selected_modes:
        filtered = filtered[filtered["mode"].isin(selected_modes)]
    if search_name.strip():
        q = search_name.strip().lower()
        filtered = filtered[
            filtered["student_names"].astype(str).str.lower().str.contains(q, na=False)
            | filtered["team_name"].astype(str).str.lower().str.contains(q, na=False)
        ]

    if filtered.empty:
        st.warning("No results match the current filters.")
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Submissions", len(filtered))
    c2.metric("Average Score", f"{filtered['score'].mean():.1f}")
    c3.metric("Average Time", fmt_seconds(filtered["actual_seconds"].mean()))
    c4.metric("Average Hints", f"{filtered['hints'].mean():.1f}")
    c5.metric("Average Attempts", f"{filtered['wrong_attempts'].mean():.1f}")

    st.caption(
        f"Score range: {filtered['score'].min():.0f}–{filtered['score'].max():.0f} | "
        f"Median completion time: {fmt_seconds(filtered['actual_seconds'].median())}"
    )

    st.markdown("#### Student/Team Detail")
    detail = filtered[[
        "timestamp", "student_names", "section", "mode", "team_name", "score",
        "actual_seconds", "adjusted_seconds", "hints", "wrong_attempts"
    ]].copy()
    detail["timestamp"] = detail["timestamp"].dt.strftime("%Y-%m-%d %I:%M %p")
    detail["actual_time"] = detail["actual_seconds"].apply(fmt_seconds)
    detail["adjusted_time"] = detail["adjusted_seconds"].apply(fmt_seconds)
    detail = detail.drop(columns=["actual_seconds", "adjusted_seconds"])
    detail = detail.rename(columns={
        "timestamp": "Completed", "student_names": "Student(s)", "section": "Section",
        "mode": "Mode", "team_name": "Team", "score": "Score", "hints": "Hints",
        "wrong_attempts": "Wrong Attempts", "actual_time": "Actual Time", "adjusted_time": "Adjusted Time"
    })
    st.dataframe(detail, use_container_width=True, hide_index=True)

    room_summary_rows = []
    for i, room in enumerate(ROOMS, start=1):
        seconds_col = f"room{i}_seconds"
        attempts_col = f"room{i}_attempts"
        hints_col = f"room{i}_hints"
        room_summary_rows.append({
            "Room": room,
            "Average Time (min)": round(filtered[seconds_col].mean() / 60, 1),
            "Average Wrong Attempts": round(filtered[attempts_col].mean(), 2),
            "Hint Use %": round((filtered[hints_col].gt(0).mean() * 100), 1),
        })
    room_df = pd.DataFrame(room_summary_rows)

    st.markdown("#### Room Performance")
    st.dataframe(room_df, use_container_width=True, hide_index=True)

    hardest_idx = room_df["Average Wrong Attempts"].idxmax()
    slowest_idx = room_df["Average Time (min)"].idxmax()
    h1, h2 = st.columns(2)
    with h1:
        st.info(
            f"Most errors: {room_df.loc[hardest_idx, 'Room']} "
            f"({room_df.loc[hardest_idx, 'Average Wrong Attempts']} average wrong attempts)."
        )
    with h2:
        st.info(
            f"Longest average time: {room_df.loc[slowest_idx, 'Room']} "
            f"({room_df.loc[slowest_idx, 'Average Time (min)']} minutes)."
        )

    chart_df = room_df.set_index("Room")[["Average Time (min)"]]
    st.markdown("#### Average Time by Room")
    st.bar_chart(chart_df)

    export_df = filtered.copy()
    export_df["timestamp"] = export_df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    st.download_button(
        "Download Filtered Results CSV",
        data=export_df.to_csv(index=False).encode("utf-8"),
        file_name="NUT3302_escape_room_class_results.csv",
        mime="text/csv",
        use_container_width=True,
    )

    with st.expander("Instructor answer key"):
        st.write("Room 1: EAR, RDA/AI, UL, AMDR, EER, %DV")
        st.write("Room 2: 8 mg, 18 mg, 27 mg; use the DRI that matches age, sex, and life stage")
        st.write("Room 3: 10% DV and 20% DV; 20% is high; compare serving size and %DV")
        st.write("Room 4: A fits, B does not fit, C fits, D does not fit")
        st.write("Room 5: RDA/AI, UL, %DV, Dietary Guidelines, then integrate each tool by purpose")
        st.write("Final word: GUIDE")


def render_faculty_dashboard():
    st.title("NUT 3302 Faculty Dashboard")
    st.subheader("Week 1 Day 2 Escape Room")

    try:
        expected_password = st.secrets.get("faculty_password", "")
    except Exception:
        expected_password = ""
    if not expected_password:
        st.warning("Set `faculty_password` in Streamlit Secrets before using the dashboard.")
        return

    if not st.session_state.faculty_authenticated:
        entered = st.text_input("Faculty password", type="password")
        if st.button("Open Dashboard", type="primary"):
            if entered == expected_password:
                st.session_state.faculty_authenticated = True
                st.rerun()
            else:
                st.error("Incorrect faculty password.")
        return

    top1, top2 = st.columns([4, 1])
    with top1:
        st.success("Faculty dashboard unlocked.")
    with top2:
        if st.button("Log out"):
            st.session_state.faculty_authenticated = False
            st.rerun()

    if google_backend_configured():
        try:
            df = load_results_from_sheet()
            dashboard_from_dataframe(df)
        except Exception as exc:
            st.error("I could not load the shared Google Sheet results.")
            st.code(str(exc))
    else:
        st.warning("Google Sheets storage is not configured. You can still review downloaded CSV results below.")
        uploads = st.file_uploader(
            "Upload student result CSV files",
            type="csv",
            accept_multiple_files=True,
        )
        if uploads:
            frames = []
            for f in uploads:
                try:
                    frames.append(pd.read_csv(f))
                except Exception:
                    pass
            if frames:
                dashboard_from_dataframe(pd.concat(frames, ignore_index=True))


# -----------------------------
# APP NAVIGATION
# -----------------------------
app_view = st.sidebar.radio("View", ["Student Escape Room", "Faculty Dashboard"], key="app_view")
if app_view == "Faculty Dashboard":
    render_faculty_dashboard()
    st.stop()

# -----------------------------
# LANDING PAGE
# -----------------------------
st.title("NUT 3302: Nutrition Standards Escape Room")
st.subheader("Week 1 • Day 2 | Nutrition Standards and Guidelines")
st.write(
    "A nutrition reference system has been scrambled. Your job is to restore the standards, "
    "solve each clinical lock, collect five letters, and use them to escape."
)

if not st.session_state.started:
    st.info(
        "Target completion time: about 30–40 minutes. There is no room time limit. "
        "The app records how long you spend in each room and your total completion time."
    )
    c1, c2 = st.columns(2)
    with c1:
        mode = st.radio("How are you completing the escape room?", ["Individual", "Team"], horizontal=True)
        names = st.text_input("Student name(s)", placeholder="Enter all names")
        section = st.selectbox("Course section", ["Select section", "Section 1", "Section 2", "Section 3"])
    with c2:
        team_name = st.text_input("Team name", disabled=(mode == "Individual"))
        st.markdown("#### Scoring")
        st.write("Your score combines accuracy, attempts, hints, and completion time.")
        st.write("Each hint costs 5 points and adds a 2-minute time penalty.")
        st.write("Each incorrect submission costs 2 points.")

    if st.button("Enter the Escape Room", type="primary", use_container_width=True):
        if not names.strip() or section == "Select section":
            st.error("Enter the student name(s) and select a course section.")
        elif mode == "Team" and not team_name.strip():
            st.error("Enter a team name.")
        else:
            st.session_state.started = True
            st.session_state.submission_id = str(uuid.uuid4())
            st.session_state.submission_saved = False
            st.session_state.submission_save_error = ""
            st.session_state.mode = mode
            st.session_state.names = names.strip()
            st.session_state.section = section
            st.session_state.team_name = team_name.strip()
            st.session_state.start_time = now()
            st.session_state.current_room = 0
            start_room(0)
            st.rerun()
    st.stop()

# -----------------------------
# SIDEBAR
# -----------------------------
with st.sidebar:
    st.header("Escape Status")
    st.write(f"Mode: {st.session_state.mode}")
    st.write(f"Section: {st.session_state.section}")
    if st.session_state.mode == "Team":
        st.write(f"Team: {st.session_state.team_name}")
    st.write(f"Elapsed: {fmt_seconds(actual_total_seconds())}")
    st.write(f"Hints used: {total_hint_count()}")
    st.write(f"Wrong attempts: {total_wrong_attempts()}")
    if st.session_state.letters:
        st.write("Letters: " + " ".join(st.session_state.letters))

show_progress()

# -----------------------------
# ROOM 1
# -----------------------------
if st.session_state.current_room == 0:
    idx = 0
    st.header(ROOMS[idx])
    st.caption("Difficulty: Moderate")
    st.markdown('<div class="room-card">', unsafe_allow_html=True)
    st.write(
        "The archive contains six nutrition standards. Match each question to the standard that best answers it. "
        "You must get all six correct to unlock the room."
    )

    options = ["Select", "EAR", "RDA or AI", "UL", "AMDR", "EER", "%DV"]
    q1 = st.selectbox("1. Which standard estimates the needs of 50% of a defined healthy group?", options, key="r1q1")
    q2 = st.selectbox("2. Which standard is used as an individual intake goal when available?", options, key="r1q2")
    q3 = st.selectbox("3. Which standard represents the highest usual intake unlikely to cause harm?", options, key="r1q3")
    q4 = st.selectbox("4. Which standard expresses macronutrients as a percentage of total energy?", options, key="r1q4")
    q5 = st.selectbox("5. Which standard estimates energy needed to maintain energy balance for a person with defined characteristics?", options, key="r1q5")
    q6 = st.selectbox("6. Which value helps compare a nutrient amount on a packaged-food label?", options, key="r1q6")

    if st.session_state.room_hints[idx] == 0:
        hint_button(idx, "Think about the purpose of each system: group adequacy, personal goal, safety ceiling, energy mix, energy need, and label comparison.")
    else:
        st.warning("Hint used: Map each item to one purpose: group need → EAR; personal goal → RDA/AI; safety → UL; energy mix → AMDR; energy need → EER; label comparison → %DV.")

    if st.button("Unlock Room 1", type="primary"):
        answers = [q1, q2, q3, q4, q5, q6]
        correct = ["EAR", "RDA or AI", "UL", "AMDR", "EER", "%DV"]
        if answers == correct:
            success_letter(idx)
            time.sleep(1.2)
            complete_room(idx)
        else:
            register_wrong(idx)
            st.error("The archive is still scrambled. At least one match is incorrect.")
    st.markdown('</div>', unsafe_allow_html=True)

# -----------------------------
# ROOM 2
# -----------------------------
elif st.session_state.current_room == 1:
    idx = 1
    st.header(ROOMS[idx])
    st.caption("Difficulty: Moderate")
    st.markdown('<div class="room-card">', unsafe_allow_html=True)
    st.write(
        "The Protein Puzzle is locked behind a food label. Use the protein information on the label "
        "to calculate calories from protein, determine % Daily Value, and classify the food correctly."
    )

    st.markdown("### Mock Nutrition Facts Label")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Calories", "210")
    with c2:
        st.metric("Protein", "14 g")
    with c3:
        st.metric("Protein Daily Value", "50 g")

    calories_from_protein = st.number_input(
        "1. How many calories in one serving come from protein?",
        min_value=0, max_value=500, step=1, key="r2_calories"
    )

    protein_pct = st.number_input(
        "2. Approximately what % Daily Value of protein does one serving provide?",
        min_value=0, max_value=100, step=1, key="r2_pct"
    )

    classify_high = st.radio(
        "3. How should this food be classified based on its protein %DV?",
        [
            "Low in protein",
            "Good source of protein",
            "High/excellent source of protein",
            "Cannot determine from the label"
        ],
        index=None,
        key="r2_classify_high"
    )

    st.markdown("### Second Label Clue")
    st.write("A second food contains **6 g of protein per serving**. The protein Daily Value is still **50 g**.")

    second_pct = st.number_input(
        "4. What % Daily Value of protein does the second food provide?",
        min_value=0, max_value=100, step=1, key="r2_second_pct"
    )

    classify_good = st.radio(
        "5. How should the second food be classified?",
        [
            "Low in protein",
            "Good source of protein",
            "High/excellent source of protein",
            "Cannot determine from the label"
        ],
        index=None,
        key="r2_classify_good"
    )

    if st.session_state.room_hints[idx] == 0:
        hint_button(
            idx,
            "Protein provides 4 kcal per gram. For %DV, divide grams per serving by the 50 g Daily Value and multiply by 100. "
            "Remember: 5% DV or less is low, 10–19% DV is a good source, and 20% DV or more is high."
        )
    else:
        st.warning(
            "Hint used: 14 g protein × 4 kcal/g = 56 kcal. "
            "14 ÷ 50 × 100 = 28% DV, which is high. "
            "6 ÷ 50 × 100 = 12% DV, which is a good source."
        )

    if st.button("Unlock Room 2", type="primary"):
        correct = (
            calories_from_protein == 56 and
            protein_pct == 28 and
            classify_high == "High/excellent source of protein" and
            second_pct == 12 and
            classify_good == "Good source of protein"
        )
        if correct:
            success_letter(idx)
            time.sleep(1.2)
            complete_room(idx)
        else:
            register_wrong(idx)
            st.error(
                "The protein lock is still closed. Recheck the calorie calculation, %DV math, "
                "and the difference between a good source and a high source."
            )
    st.markdown('</div>', unsafe_allow_html=True)

# -----------------------------
# ROOM 3
# -----------------------------
elif st.session_state.current_room == 2:
    idx = 2
    st.header(ROOMS[idx])
    st.caption("Difficulty: Moderate to Difficult")
    st.markdown('<div class="room-card">', unsafe_allow_html=True)
    st.write(
        "The Label Lab has two products, but the comparison screen is offline. Calculate the sodium %DV for each and choose the better interpretation."
    )

    st.markdown("**Product A:** 230 mg sodium per serving")
    st.markdown("**Product B:** 460 mg sodium per serving")
    st.markdown("**Sodium Daily Value:** 2,300 mg")

    a_pct = st.number_input("Product A sodium %DV", min_value=0, max_value=100, step=1, key="r3a")
    b_pct = st.number_input("Product B sodium %DV", min_value=0, max_value=100, step=1, key="r3b")

    interpret = st.radio(
        "Which interpretation is most accurate using the 5–20 guide?",
        [
            "Product A is low sodium because 10% DV is less than 20%.",
            "Product B is high in sodium because it provides 20% DV per serving.",
            "Both products are low in sodium.",
            "The 5–20 guide cannot be used for sodium."
        ],
        index=None,
        key="r3interpret"
    )

    clinical = st.radio(
        "A patient wants to reduce sodium. Which teaching statement best uses the label?",
        [
            "Choose the product with the lowest calories because calories determine sodium exposure.",
            "Compare serving size and sodium %DV, then choose the option that better fits the patient's overall plan.",
            "Avoid every food above 5% DV sodium.",
            "Use the sodium UL as the patient's daily intake goal."
        ],
        index=None,
        key="r3clinical"
    )

    if st.session_state.room_hints[idx] == 0:
        hint_button(idx, "Use: amount per serving ÷ Daily Value × 100. Remember: 5% or less is low; 20% or more is high.")
    else:
        st.warning("Hint used: 230 ÷ 2300 × 100 = 10%. Doubling 230 mg doubles the %DV. A value of 20% meets the 'high' threshold.")

    if st.button("Unlock Room 3", type="primary"):
        if (
            a_pct == 10 and
            b_pct == 20 and
            interpret == "Product B is high in sodium because it provides 20% DV per serving." and
            clinical == "Compare serving size and sodium %DV, then choose the option that better fits the patient's overall plan."
        ):
            success_letter(idx)
            time.sleep(1.2)
            complete_room(idx)
        else:
            register_wrong(idx)
            st.error("The label comparison is not complete. Recheck the math, the 5–20 guide, and how labels support patient teaching.")
    st.markdown('</div>', unsafe_allow_html=True)

# -----------------------------
# ROOM 4
# -----------------------------
elif st.session_state.current_room == 3:
    idx = 3
    st.header(ROOMS[idx])
    st.caption("Difficulty: Difficult")
    st.markdown('<div class="room-card">', unsafe_allow_html=True)
    st.write(
        "The AMDR Vault only opens if you identify which macronutrient patterns fit the adult ranges. "
        "Adult AMDRs: carbohydrate 45–65%, protein 10–35%, fat 20–35% of energy."
    )

    patterns = {
        "Pattern A: 55% carbohydrate, 20% protein, 25% fat": True,
        "Pattern B: 70% carbohydrate, 15% protein, 15% fat": False,
        "Pattern C: 45% carbohydrate, 35% protein, 20% fat": True,
        "Pattern D: 40% carbohydrate, 30% protein, 30% fat": False,
    }

    responses = {}
    for label in patterns:
        responses[label] = st.radio(label, ["Fits AMDR", "Does not fit AMDR"], index=None, horizontal=True, key=label)

    reasoning = st.radio(
        "Why is a pattern inside all three AMDR ranges not automatically a high-quality diet?",
        [
            "AMDR describes energy distribution, not food quality, fiber, added sugar, or overall nutrient density.",
            "AMDR only applies to hospitalized patients.",
            "AMDR replaces the need to assess the patient.",
            "AMDR measures vitamin and mineral adequacy."
        ],
        index=None,
        key="r4reason"
    )

    if st.session_state.room_hints[idx] == 0:
        hint_button(idx, "Check each macronutrient separately. One value outside its range makes the entire pattern fail.")
    else:
        st.warning("Hint used: A fits. B fails because carbohydrate is too high and fat is too low. C fits at the boundaries. D fails because carbohydrate is below 45%.")

    if st.button("Unlock Room 4", type="primary"):
        correct_resp = all(
            responses[k] == ("Fits AMDR" if v else "Does not fit AMDR")
            for k, v in patterns.items()
        )
        correct_reason = reasoning == "AMDR describes energy distribution, not food quality, fiber, added sugar, or overall nutrient density."
        if correct_resp and correct_reason:
            success_letter(idx)
            time.sleep(1.2)
            complete_room(idx)
        else:
            register_wrong(idx)
            st.error("The vault remains locked. Check every macronutrient range, not just carbohydrate.")
    st.markdown('</div>', unsafe_allow_html=True)

# -----------------------------
# ROOM 5
# -----------------------------
elif st.session_state.current_room == 4:
    idx = 4
    st.header(ROOMS[idx])
    st.caption("Difficulty: Most Difficult")
    st.markdown('<div class="room-card">', unsafe_allow_html=True)
    st.write(
        "A patient has arrived with several nutrition questions. You must choose the correct standard for each decision and avoid using one number for every purpose."
    )
    st.info(
        "Patient: 29-year-old nonpregnant woman. She takes a multivitamin plus a separate iron supplement. "
        "She compares packaged foods for sodium and wants to improve her overall eating pattern."
    )

    q1 = st.radio(
        "1. What should the nurse use first to identify her usual individual nutrient intake goal?",
        ["EAR", "RDA or AI", "UL", "%DV"],
        index=None, key="r5q1"
    )
    q2 = st.radio(
        "2. Which standard is most important when checking whether combined supplement exposure approaches a potentially harmful level?",
        ["RDA", "UL", "EER", "AMDR"],
        index=None, key="r5q2"
    )
    q3 = st.radio(
        "3. Which tool best helps her compare sodium between two packaged foods?",
        ["EAR", "EER", "%DV", "RDA"],
        index=None, key="r5q3"
    )
    q4 = st.radio(
        "4. Which source best guides the overall eating pattern over time?",
        ["Dietary Guidelines", "UL", "EAR", "One product's Nutrition Facts label"],
        index=None, key="r5q4"
    )
    q5 = st.radio(
        "5. Which nursing sequence best integrates the standards?",
        [
            "Use %DV as the intake goal, then use UL only if symptoms occur.",
            "Choose the correct DRI for the patient, compare intake with RDA/AI, review the UL for safety, use %DV for packaged foods, and use dietary guidance to build the eating pattern.",
            "Use the EAR for the individual goal and the RDA as the safety ceiling.",
            "Use one standard consistently so the patient does not become confused."
        ],
        index=None, key="r5q5"
    )

    if st.session_state.room_hints[idx] == 0:
        hint_button(idx, "Think in this order: Who is the patient? What is the goal? Is there a safety ceiling? Are we comparing a label? Are we building an eating pattern?")
    else:
        st.warning("Hint used: Personal goal → RDA/AI. Supplement safety → UL. Packaged-food comparison → %DV. Overall pattern → Dietary Guidelines.")

    if st.button("Unlock Final Room", type="primary"):
        correct = (
            q1 == "RDA or AI" and
            q2 == "UL" and
            q3 == "%DV" and
            q4 == "Dietary Guidelines" and
            q5 == "Choose the correct DRI for the patient, compare intake with RDA/AI, review the UL for safety, use %DV for packaged foods, and use dietary guidance to build the eating pattern."
        )
        if correct:
            success_letter(idx)
            time.sleep(1.2)
            complete_room(idx)
        else:
            register_wrong(idx)
            st.error("The patient plan still mixes standards. Match each question to the tool designed to answer it.")
    st.markdown('</div>', unsafe_allow_html=True)


# -----------------------------
# ROOM 6
# -----------------------------
elif st.session_state.current_room == 5:
    idx = 5
    st.header(ROOMS[idx])
    st.caption("Difficulty: Difficult • Week 1 Day 1 Review")
    st.markdown('<div class="room-card">', unsafe_allow_html=True)
    st.write(
        "A campus café has asked you to help students make sense of food choices. "
        "Use nutrient density, energy density, and the factors that influence why people eat."
    )

    q1 = st.radio(
        "1. Which statement best describes a nutrient-dense food?",
        [
            "It always contains fewer than 100 calories.",
            "It provides meaningful nutrients relative to the calories it contains.",
            "It contains no fat or added sugar.",
            "It must be a fruit or vegetable."
        ],
        index=None, key="r6q1"
    )
    q2 = st.radio(
        "2. Which food can be energy dense and still contribute valuable nutrients?",
        [
            "Almonds",
            "Diet soda",
            "Hard candy",
            "Ice pop"
        ],
        index=None, key="r6q2"
    )
    q3 = st.radio(
        "3. A student is not physically hungry but buys pizza because everyone in the study group is eating. "
        "What is the strongest influence on this food choice?",
        [
            "Physiologic hunger",
            "Social influence",
            "Nutrient deficiency",
            "Energy requirement"
        ],
        index=None, key="r6q3"
    )
    q4 = st.radio(
        "4. A student chooses the only food option still available after a late lab. "
        "Which factor most directly influenced the choice?",
        [
            "Environment and availability",
            "Physiologic hunger only",
            "Cultural tradition only",
            "Daily Value"
        ],
        index=None, key="r6q4"
    )
    q5 = st.radio(
        "5. Which statement about energy-dense foods is most accurate?",
        [
            "Energy-dense foods are automatically unhealthy.",
            "Energy density tells you how many vitamins a food contains.",
            "Energy-dense foods provide more calories relative to their weight or amount, but nutrient quality still matters.",
            "Only foods high in fat are energy dense."
        ],
        index=None, key="r6q5"
    )

    if st.session_state.room_hints[idx] == 0:
        hint_button(
            idx,
            "Do not equate calories with quality. Think about nutrients relative to calories, and remember that food choices are shaped by biology, personal factors, resources, and environment."
        )
    else:
        st.warning(
            "Hint used: Nutrient density describes nutrients relative to calories. Nuts can be energy dense and nutrient rich. "
            "Eating because others are eating is social. Limited choices point to environment/availability."
        )

    if st.button("Unlock Room 6", type="primary"):
        correct = (
            q1 == "It provides meaningful nutrients relative to the calories it contains." and
            q2 == "Almonds" and
            q3 == "Social influence" and
            q4 == "Environment and availability" and
            q5 == "Energy-dense foods provide more calories relative to their weight or amount, but nutrient quality still matters."
        )
        if correct:
            success_letter(idx)
            time.sleep(1.2)
            complete_room(idx)
        else:
            register_wrong(idx)
            st.error("The café lock is still closed. Recheck nutrient density, energy density, and what is driving each food choice.")
    st.markdown('</div>', unsafe_allow_html=True)

# -----------------------------
# ROOM 7
# -----------------------------
elif st.session_state.current_room == 6:
    idx = 6
    st.header(ROOMS[idx])
    st.caption("Difficulty: Most Difficult • Unfolding Case Study")
    st.markdown('<div class="room-card">', unsafe_allow_html=True)
    st.write(
        "Final case: Maya is a 20-year-old college student. She reports skipping breakfast because she is rushed, "
        "buying whatever is closest between classes, and eating with friends late at night even when she is not hungry."
    )

    stage1 = st.radio(
        "Stage 1. Which assessment finding best shows an environmental influence on Maya's intake?",
        [
            "She feels hungry before lunch.",
            "She buys whatever food is closest between classes.",
            "She enjoys eating foods she grew up with.",
            "She reports liking sweet foods."
        ],
        index=None, key="r7stage1"
    )

    st.info(
        "New information: At lunch Maya chooses a snack bar with 12 g protein. "
        "Protein provides 4 kcal per gram. The label lists protein as 24% DV."
    )

    protein_kcal = st.number_input(
        "Stage 2. How many calories in the bar come from protein?",
        min_value=0, max_value=300, step=1, key="r7protein"
    )
    stage2b = st.radio(
        "How should the protein content be described using %DV?",
        [
            "Low",
            "Good source",
            "High/excellent source",
            "Cannot be determined"
        ],
        index=None, key="r7stage2b"
    )

    st.info(
        "New information: Maya asks whether the %DV on the package is the same thing as her individualized nutrient goal."
    )
    stage3 = st.radio(
        "Stage 3. What is the best nursing response?",
        [
            "%DV is your individualized intake goal.",
            "Use the RDA or AI when an individual intake goal is needed; %DV is useful for comparing packaged foods.",
            "Use the UL as your daily target.",
            "Use the EAR as the individual goal for every nutrient."
        ],
        index=None, key="r7stage3"
    )

    st.info(
        "Final information: Maya wants one realistic change. Her usual afternoon choice is candy and a sweetened drink. "
        "She has access to Greek yogurt, fruit, nuts, water, and sandwiches on campus."
    )
    stage4 = st.radio(
        "Stage 4. Which recommendation best applies the concepts from both class days?",
        [
            "Choose the item with the fewest calories every time.",
            "Avoid all energy-dense foods.",
            "Choose a nutrient-dense option such as Greek yogurt with fruit and water, while considering convenience and what she will realistically eat.",
            "Use the Daily Value as the only guide for all food decisions."
        ],
        index=None, key="r7stage4"
    )

    if st.session_state.room_hints[idx] == 0:
        hint_button(
            idx,
            "Work through the case in sequence: identify the influence on intake, calculate protein energy, interpret %DV, "
            "choose the correct nutrition standard, then make a realistic nutrient-dense recommendation."
        )
    else:
        st.warning(
            "Hint used: Closest available food = environment. 12 g protein × 4 kcal/g = 48 kcal. "
            "24% DV is high. RDA/AI supports an individual goal; %DV compares labels. "
            "The best teaching choice balances nutrient density with the student's real environment."
        )

    if st.button("Unlock Room 7", type="primary"):
        correct = (
            stage1 == "She buys whatever food is closest between classes." and
            protein_kcal == 48 and
            stage2b == "High/excellent source" and
            stage3 == "Use the RDA or AI when an individual intake goal is needed; %DV is useful for comparing packaged foods." and
            stage4 == "Choose a nutrient-dense option such as Greek yogurt with fruit and water, while considering convenience and what she will realistically eat."
        )
        if correct:
            success_letter(idx)
            time.sleep(1.2)
            complete_room(idx)
        else:
            register_wrong(idx)
            st.error("The final patient lock is still closed. Follow the case from assessment through calculation, label interpretation, and teaching.")
    st.markdown('</div>', unsafe_allow_html=True)

# -----------------------------
# FINAL ESCAPE
# -----------------------------
elif st.session_state.current_room == 7 and not st.session_state.finished:
    st.header("Final Escape: Rebuild the Key")
    st.caption("Difficulty: Final synthesis")
    st.write("You collected seven letters:")
    cols = st.columns(5)
    for i, letter in enumerate(st.session_state.letters):
        with cols[i]:
            st.markdown(f'<div class="letter-box">{letter}</div>', unsafe_allow_html=True)

    st.write(
        "Rearrange the seven letters to form a word that captures a central idea in nutrition: meeting needs while considering quality, energy, and the whole eating pattern."
    )

    final_word = st.text_input("Final escape word", max_chars=10).strip().upper()

    if not st.session_state.final_hint_used:
        if st.button("Use final hint (5 points + 2 minutes)"):
            st.session_state.final_hint_used = True
            st.warning("Hint: Nutrition is not about one perfect food or one number. Think about the overall _____.")
    else:
        st.warning("Hint used: Think about the word used when nutrients, energy, food quality, and the overall eating pattern work together: BALANCE.")

    if st.button("ESCAPE", type="primary", use_container_width=True):
        if final_word == FINAL_WORD:
            st.session_state.final_finish_time = now()
            st.session_state.finish_time = now()
            st.session_state.finished = True
            st.rerun()
        else:
            st.session_state.final_attempts += 1
            st.error("The final word is not correct. Use all seven letters.")

# -----------------------------
# RESULTS
# -----------------------------
if st.session_state.finished:
    score, accuracy_score, efficiency_score = calculate_score()
    save_result_to_sheet()
    st.balloons()
    st.header("Escape Complete")
    st.success(f"You escaped with the word: {FINAL_WORD}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Final Score", f"{score}/100")
    c2.metric("Actual Time", fmt_seconds(actual_total_seconds()))
    c3.metric("Adjusted Time", fmt_seconds(adjusted_total_seconds()))
    c4.metric("Hints Used", total_hint_count())

    st.caption(
        f"Adjusted time includes {fmt_seconds(penalty_seconds())} in hint penalties. "
        f"Accuracy component: {accuracy_score}/80. Efficiency component: {efficiency_score}/20."
    )

    st.subheader("Room-by-Room Performance")
    rows = []
    for i, room in enumerate(ROOMS):
        rows.append({
            "Room": room,
            "Time": fmt_seconds(room_elapsed(i)),
            "Wrong Attempts": st.session_state.room_attempts[i],
            "Hints": st.session_state.room_hints[i],
            "Letter": LETTERS[i],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.subheader("Debrief")
    st.write(
        "The escape word is GUIDE because nutrition standards guide decisions. "
        "They do not replace individualized assessment or nursing judgment."
    )
    st.write(
        "Key idea: use the right standard for the question. EAR addresses group adequacy. "
        "RDA or AI supports an individual intake goal. UL addresses safety. AMDR addresses energy distribution. "
        "EER estimates energy needs. %DV supports label comparison. Dietary guidance supports eating patterns."
    )

    if st.session_state.submission_saved:
        st.success("Your result was submitted to the faculty dashboard.")
    elif st.session_state.submission_save_error:
        st.warning("Your score is complete, but it was not added to the central dashboard. Download the result below and submit it to your instructor.")

    # Downloadable single-row result
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(RESULT_HEADERS)
    writer.writerow(build_result_record())

    st.download_button(
        "Download Result for Instructor",
        data=output.getvalue(),
        file_name="NUT3302_escape_room_result.csv",
        mime="text/csv"
    )

    with st.expander("How your score was calculated"):
        st.write("Start with 80 accuracy points.")
        st.write(f"Each incorrect submission deducts {WRONG_ATTEMPT_PENALTY} points.")
        st.write(f"Each hint deducts {HINT_POINT_PENALTY} points and adds 2 minutes to adjusted time.")
        st.write("Efficiency adds up to 20 points based on adjusted completion time.")
        st.write("30 min or less = 20 points; 31–35 = 17; 36–40 = 14; 41–45 = 10; 46–50 = 6; over 50 = 3.")
