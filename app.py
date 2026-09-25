import base64
from datetime import datetime, timedelta
import io
import json
import os
import time
import streamlit as st
from google import genai
from google.genai import types
from google.genai.errors import APIError
from PIL import Image

# Pfad für die zentrale Datenspeicherung
DATA_FILE = "events.json"

st.set_page_config(
    page_title="Event Planner", page_icon="🎉", layout="wide"
)

# ---------------------------------------------------------
# Custom CSS: Saubere Karten-Optik & Tab-Styling
# ---------------------------------------------------------
st.markdown("""
<style>
    /* Styling für die Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 40px;
        background-color: #f0f2f6;
        border-radius: 8px 8px 0px 0px;
        padding-left: 14px;
        padding-right: 14px;
        font-weight: 600;
        border: 1px solid #e0e0e0;
        border-bottom: none;
    }
    .stTabs [aria-selected="true"] {
        background-color: #ff4b4b !important;
        color: white !important;
        border-color: #ff4b4b !important;
    }

    /* Einheitliche Popover- & Button-Höhe in Event-Karten */
    div[data-testid="column"] div[data-testid="stPopover"] > button,
    div[data-testid="column"] .stButton > button {
        width: 100% !important;
        height: 42px !important;
        font-size: 0.85rem !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------
def format_german_date(date_iso_str):
    try:
        dt = datetime.strptime(date_iso_str, "%Y-%m-%d")
        weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        return f"{weekdays[dt.weekday()]}, {dt.day}. {months[dt.month - 1]} {dt.year}"
    except Exception:
        return date_iso_str

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "users" not in data or not isinstance(data["users"], list):
                    if isinstance(data.get("users"), dict):
                        data["users"] = list(data["users"].keys())
                    else:
                        data["users"] = ["Anna", "Julian", "Matthias"]
                if "events" not in data:
                    data["events"] = []
                data["users"] = sorted(data["users"], key=str.lower)
                return data
        except Exception:
            return {"users": ["Anna", "Julian", "Matthias"], "events": []}
    return {"users": ["Anna", "Julian", "Matthias"], "events": []}

def save_data(data):
    if "users" in data and isinstance(data["users"], list):
        data["users"] = sorted(data["users"], key=str.lower)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def image_to_base64(image):
    buffered = io.BytesIO()
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    image.thumbnail((800, 800))
    image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

# ---------------------------------------------------------
# Session State Initialisierung
# ---------------------------------------------------------
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

if "pending_event" not in st.session_state:
    st.session_state.pending_event = None

# ---------------------------------------------------------
# Secrets & Daten laden
# ---------------------------------------------------------
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    api_key = None
    st.error("Fehler: Kein 'GEMINI_API_KEY' in den Streamlit Secrets hinterlegt!")

ADMIN_PIN = st.secrets.get("ADMIN_PIN", "1234")
data = load_data()

# ---------------------------------------------------------
# Sidebar: Admin-Modus
# ---------------------------------------------------------
st.sidebar.title("🛠️ Admin & Konfig")

admin_pin_input = st.sidebar.text_input("Admin-PIN eingeben", type="password")
is_admin = admin_pin_input == ADMIN_PIN

if is_admin:
    st.sidebar.success("🔑 Admin-Modus aktiv")

    with st.sidebar.expander("👥 Teilnehmer verwalten", expanded=True):
        new_user_input = st.text_input("Neuer Name")
        if st.button("➕ Hinzufügen"):
            clean_name = new_user_input.strip()
            if clean_name and clean_name not in data["users"]:
                data["users"].append(clean_name)
                save_data(data)
                st.sidebar.success(f"'{clean_name}' hinzugefügt!")
                st.rerun()

        st.divider()
        st.write("Bestehende Teilnehmer (alphabetisch):")
        for uname in list(data["users"]):
            ucol1, ucol2 = st.columns([3, 1])
            ucol1.write(f"• {uname}")
            if ucol2.button("🗑️", key=f"del_user_{uname}"):
                data["users"].remove(uname)
                for event in data.get("events", []):
                    if uname in event.get("voters", []):
                        event["voters"].remove(uname)
                        event["votes"] = len(event["voters"])
                save_data(data)
                st.rerun()

    with st.sidebar.expander("🧹 Archiv aufräumen", expanded=False):
        cutoff_date = st.date_input("Lösche Events vor Datum:", value=datetime.now().date() - timedelta(days=30))
        if st.button("🗑️ Alte Events löschen", type="primary"):
            cutoff_str = cutoff_date.strftime("%Y-%m-%d")
            data["events"] = [e for e in data["events"] if e.get("date_iso", "9999-99-99") >= cutoff_str]
            save_data(data)
            st.rerun()

# ---------------------------------------------------------
# HAUPTSEITE: Wer bist du?
# ---------------------------------------------------------
st.title("🎉 Event Planner")

users_list = sorted(data.get("users", []), key=str.lower)
if not users_list:
    st.warning("⚠️ Keine Teilnehmer definiert. Bitte im Admin-Bereich anlegen.")
    st.stop()

with st.container(border=True):
    st.markdown("### 👤 Wer bist du?")
    selected_user = st.selectbox(
        "Dein Name:", 
        options=users_list, 
        index=None, 
        placeholder="-- Bitte wähle zuerst deinen Namen aus --", 
        label_visibility="collapsed"
    )

st.write("")

# ---------------------------------------------------------
# KI-Analyse & Daten-Sortierung
# ---------------------------------------------------------
def analyze_flyer(image, key, max_retries=3):
    client = genai.Client(api_key=key)
    prompt = """Analysiere diesen Flyer. Extrahiere als JSON:
    {"title": "Name", "date_iso": "YYYY-MM-DD", "date_display": "z.B. Samstag, 15. Okt", "time": "Uhrzeit", "location": "Ort", "description": "1 Satz Zusammenfassung"}"""
    
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[image, prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return json.loads(response.text)
        except APIError as e:
            if attempt < max_retries: time.sleep(attempt * 2)
            else: raise e

events = data.get("events", [])
today = datetime.now().date()
end_of_week = today + timedelta(days=7)

current_week_events, future_events, past_events = [], [], []
seen_ids = set()

for ev in events:
    ev_id = ev.get("id")
    if ev_id in seen_ids: continue
    seen_ids.add(ev_id)

    raw_date = ev.get("date_iso", "")
    try: event_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except: event_date = datetime(2099, 12, 31).date()

    if event_date < today: past_events.append(ev)
    elif today <= event_date <= end_of_week: current_week_events.append(ev)
    else: future_events.append(ev)

# ---------------------------------------------------------
# Tabs definieren
# ---------------------------------------------------------
tab_current, tab_future, tab_upload, tab_past = st.tabs([
    f"🔥 Diese Woche ({len(current_week_events)})",
    f"🔮 Demnächst ({len(future_events)})",
    "➕ Neuer Flyer",
    f"📦 Archiv ({len(past_events)})",
])

# ---------------------------------------------------------
# Hilfsfunktion zum Rendern der Listen (Kompaktes Layout)
# ---------------------------------------------------------
def render_event_list(event_list, is_past=False):
    if not event_list:
        st.info("Keine Events in dieser Kategorie.")
        return

    sorted_events = sorted(event_list, key=lambda x: (x.get("date_iso", "9999-99-99"), x.get("time", "")))
    last_date = None

    for idx, event in enumerate(sorted_events):
        event_date_iso = event.get("date_iso", "")
        if event_date_iso != last_date:
            st.markdown(f"##### 📅 {format_german_date(event_date_iso)}")
            last_date = event_date_iso

        event_id = event.get("id", str(idx))
        voters_list = sorted(event.get("voters", []), key=str.lower)
        has_voted = selected_user in voters_list if selected_user else False

        # Kompakte Event-Karte
        with st.container(border=True):
            # 1. Flyer-Bild
            img_b64 = event.get("image_base64", "")
            if img_b64:
                try:
                    img_bytes = base64.b64decode(img_b64)
                    st.image(img_bytes, use_container_width=True) 
                except Exception:
                    pass

            # 2. Direkt sichtbare Kerninformationen (Kein Expander nötig!)
            event_title = event.get("title", "Unbekanntes Event")
            time_str = event.get("time", "")
            loc_str = event.get("location", "")
            
            st.markdown(f"### {event_title}")
            meta_info = []
            if time_str: meta_info.append(f"⏰ {time_str}")
            if loc_str: meta_info.append(f"📍 {loc_str}")
            if meta_info:
                st.caption(" • ".join(meta_info))

            st.write("")

            # 3. Aktionsleiste: Popover für Zusagen & Popover für Details (2-Spalten-Layout)
            col_rsvp, col_info = st.columns([1, 1])

            # SPALTE 1: ZUSAGEN-POPOVER
            with col_rsvp:
                if is_past:
                    st.button("🔒 Vorbei", disabled=True, use_container_width=True, key=f"dis_{event_id}")
                else:
                    # Dynamisches Label: Zeigt die Anzahl und ob DU zugesagt hast
                    status_text = f"👥 {len(voters_list)} Zusage(n)"
                    if has_voted:
                        status_text += " • 👍"

                    with st.popover(status_text, use_container_width=True):
                        st.markdown(f"#### 👥 Zusagen ({len(voters_list)})")
                        if voters_list:
                            for voter in voters_list:
                                st.write(f"• {voter}")
                        else:
                            st.info("Noch keine Zusagen.")

                        st.divider()

                        if not selected_user:
                            st.warning("Bitte wähle oben zuerst deinen Namen aus.")
                        else:
                            vote_btn_text = "❌ Zusage zurückziehen" if has_voted else "👍 Ich bin dabei!"
                            vote_btn_type = "secondary" if has_voted else "primary"
                            
                            if st.button(vote_btn_text, type=vote_btn_type, key=f"pop_vote_{event_id}_{selected_user}", use_container_width=True):
                                if has_voted:
                                    voters_list.remove(selected_user)
                                else:
                                    voters_list.append(selected_user)
                                event["voters"] = voters_list
                                event["votes"] = len(voters_list)
                                save_data(data)
                                st.rerun()

            # SPALTE 2: DETAILS-POPOVER
            with col_info:
                with st.popover("ℹ️ Details", use_container_width=True):
                    st.markdown(f"#### 📌 {event_title}")
                    st.write(f"📅 **Datum:** {event.get('date_display', event.get('date_iso', 'N/A'))}")
                    st.write(f"⏰ **Uhrzeit:** {time_str if time_str else 'N/A'}")
                    st.write(f"📍 **Ort:** {loc_str if loc_str else 'N/A'}")
                    if event.get("description"):
                        st.divider()
                        st.write(event.get("description"))

            # 4. Admin-Bereich (Nur sichtbar für Admins)
            if is_admin:
                st.write("")
                with st.popover("🛠️ Admin-Optionen", use_container_width=True):
                    st.markdown("##### Event verwalten")
                    if st.button("🗑️ Event Löschen", key=f"del_{event_id}", type="primary", use_container_width=True):
                        data["events"] = [e for e in data["events"] if e.get("id") != event_id]
                        save_data(data)
                        st.rerun()
                    
                    st.divider()
                    st.markdown("##### Daten bearbeiten")
                    event["title"] = st.text_input("Titel", value=event.get("title"), key=f"ed_t_{event_id}")
                    event["date_display"] = st.text_input("Anzeige", value=event.get("date_display"), key=f"ed_d_{event_id}")
                    event["date_iso"] = st.text_input("ISO (YYYY-MM-DD)", value=event.get("date_iso"), key=f"ed_iso_{event_id}")
                    event["location"] = st.text_input("Ort", value=event.get("location"), key=f"ed_l_{event_id}")
                    if st.button("Speichern", key=f"save_ed_{event_id}", use_container_width=True):
                        save_data(data)
                        st.rerun()

# ---------------------------------------------------------
# Tab-Inhalte zuweisen
# ---------------------------------------------------------
with tab_current: render_event_list(current_week_events, is_past=False)
with tab_future: render_event_list(future_events, is_past=False)
with tab_past: render_event_list(past_events, is_past=True)

# ---------------------------------------------------------
# TAB UPLOAD
# ---------------------------------------------------------
with tab_upload:
    st.header("Flyer analysieren")
    uploaded_file = st.file_uploader("Bild auswählen", type=["png", "jpg", "jpeg"], key=f"up_{st.session_state.uploader_key}")

    if uploaded_file and api_key and not st.session_state.pending_event:
        if st.button("🔍 Analysieren"):
            with st.spinner("Gemini liest den Flyer..."):
                img = Image.open(uploaded_file)
                ext = analyze_flyer(img, api_key)
                ext["image_base64"] = image_to_base64(img)
                st.session_state.pending_event = ext
                st.rerun()

    if st.session_state.pending_event:
        st.subheader("📋 Daten prüfen")
        p = st.session_state.pending_event
        if "image_base64" in p:
            st.image(base64.b64decode(p["image_base64"]), width=200)

        t = st.text_input("Titel", value=p.get("title", ""))
        d1 = st.text_input("Datum Anzeige", value=p.get("date_display", ""))
        d2 = st.text_input("Datum ISO", value=p.get("date_iso", datetime.now().strftime("%Y-%m-%d")))
        tm = st.text_input("Uhrzeit", value=p.get("time", ""))
        l = st.text_input("Ort", value=p.get("location", ""))
        ds = st.text_area("Beschreibung", value=p.get("description", ""))

        c1, c2 = st.columns(2)
        if c1.button("🚀 Speichern", type="primary"):
            data["events"].append({
                "id": str(int(time.time())), "title": t, "date_display": d1, "date_iso": d2, 
                "time": tm, "location": l, "description": ds, "votes": 0, "voters": [], 
                "image_base64": p.get("image_base64", "")
            })
            save_data(data)
            st.session_state.pending_event = None
            st.session_state.uploader_key += 1
            st.rerun()
        if c2.button("❌ Abbrechen"):
            st.session_state.pending_event = None
            st.session_state.uploader_key += 1
            st.rerun()