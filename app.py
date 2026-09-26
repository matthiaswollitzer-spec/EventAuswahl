import base64
import datetime
import io
import json
import requests
import PIL.Image
import streamlit as st

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

# ---------------------------------------------------------
# 1. PAGE CONFIGURATION & CUSTOM CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title="Event Planner",
    page_icon="📅",
    layout="centered",
    initial_sidebar_state="collapsed"
)

st.markdown("""
    <style>
    .stApp {
        max-width: 800px;
        margin: 0 auto;
    }
    .date-header {
        display: flex;
        align-items: center;
        text-align: center;
        color: #555;
        font-weight: bold;
        margin-top: 25px;
        margin-bottom: 15px;
    }
    .date-header::before, .date-header::after {
        content: '';
        flex: 1;
        border-bottom: 2px solid #ddd;
    }
    .date-header:not(:empty)::before {
        margin-right: .75em;
    }
    .date-header:not(:empty)::after {
        margin-left: .75em;
    }
    .stButton button {
        width: 100%;
        border-radius: 8px;
    }
    </style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------
# 2. GOOGLE DRIVE PERSISTENCE
# ---------------------------------------------------------
def get_drive_service():
    creds_dict = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build('drive', 'v3', credentials=creds)


def load_data():
    try:
        service = get_drive_service()
        folder_id = st.secrets["DRIVE_FOLDER_ID"]

        query = f"'{folder_id}' in parents and name = 'data.json' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])

        if not files:
            return {"users": ["Anna", "Julian", "Matthias"], "events": []}

        file_id = files[0]['id']
        request = service.files().get_media(fileId=file_id)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        file_stream.seek(0)
        return json.loads(file_stream.read().decode('utf-8'))
    except Exception as e:
        st.error(f"Fehler beim Laden von Google Drive: {e}")
        return {"users": ["Anna", "Julian", "Matthias"], "events": []}


def save_data(data):
    try:
        service = get_drive_service()
        folder_id = st.secrets["DRIVE_FOLDER_ID"]

        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        media = MediaInMemoryUpload(json_bytes, mimetype='application/json', resumable=True)

        query = f"'{folder_id}' in parents and name = 'data.json' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])

        if files:
            file_id = files[0]['id']
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            file_metadata = {'name': 'data.json', 'parents': [folder_id]}
            service.files().create(body=file_metadata, media_body=media).execute()

        return True
    except Exception as e:
        st.error(f"Fehler beim Speichern in Google Drive: {e}")
        return False


# ---------------------------------------------------------
# 3. HELPER FUNCTIONS
# ---------------------------------------------------------
def image_to_base64(image):
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def analyze_flyer_with_gemini(image_bytes):
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        st.error("Kein GEMINI_API_KEY in den Secrets konfiguriert.")
        return None

    try:
        b64_image = base64.b64encode(image_bytes).decode('utf-8')
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={api_key}"

        prompt = """
        Analysiere diesen Event-Flyer und extrahiere die folgenden Informationen als JSON:
        {
          "title": "Titel des Events",
          "date": "YYYY-MM-DD",
          "time": "HH:MM",
          "location": "Ort / Location",
          "description": "Kurze Event-Beschreibung"
        }
        Falls ein Feld nicht auf dem Flyer steht, gib als Wert einen leeren String "" an.
        Antworte AUSSCHLIESSLICH im reinen JSON-Format ohne Markdown-Codeblöcke.
        """

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}}
                ]
            }]
        }

        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"})
        if res.status_code == 200:
            text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        else:
            st.error(f"Gemini API Fehler ({res.status_code}): {res.text}")
            return None
    except Exception as e:
        st.error(f"Fehler bei der KI-Analyse: {e}")
        return None


def format_german_date(date_obj):
    weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    weekday = weekdays[date_obj.weekday()]
    return f"{weekday}, {date_obj.strftime('%d.%m.%Y')}"


today = datetime.date.today()
end_of_7_days = today + datetime.timedelta(days=7)

def get_target_tab_name(date_obj):
    if not isinstance(date_obj, datetime.date):
        return "🔮 Zukünftig"
    if today <= date_obj <= end_of_7_days:
        return "📍 Aktuelle Woche"
    elif date_obj > end_of_7_days:
        return "🔮 Zukünftig"
    else:
        return "📜 Vergangen"


def is_duplicate_event(data, title, date_str, current_event_id=None):
    clean_title = title.strip().lower()
    clean_date = date_str.strip()

    for ev in data.get("events", []):
        if current_event_id and ev.get("id") == current_event_id:
            continue
        if ev.get("title", "").strip().lower() == clean_title and ev.get("date", "").strip() == clean_date:
            return True
    return False


# ---------------------------------------------------------
# 4. SINGLE EVENT CARD RENDERER
# Structure: 1. Image -> 2. Zusagen Dropdown -> 3. Event Daten Dropdown
# ---------------------------------------------------------
def render_single_event_card(ev, is_preview=False, is_admin=False):
    ev_id = ev.get("id", "preview_id")

    # 1. IMAGE DISPLAY (ROBUST BASE64 DECODING)
    flyer_data = ev.get("flyer_b64") or ev.get("image_b64")
    if flyer_data:
        try:
            if "," in flyer_data:
                flyer_data = flyer_data.split(",")[1]
            img_bytes = base64.b64decode(flyer_data)
            st.image(img_bytes, use_container_width=True)
        except Exception as e:
            st.caption("⚠️ Bild konnte nicht angezeigt werden.")

    # 2. ZUSAGEN DROPDOWN
    participants = ev.get("participants", [])
    current_user = st.session_state.get("current_user")
    has_user = current_user and current_user != "-- Bitte wählen --"
    is_attending = has_user and (current_user in participants)

    with st.expander(f"👥 Zusagen ({len(participants)})", expanded=False):
        if participants:
            st.write("**Teilnehmer:**")
            for p in participants:
                st.write(f"- {p}")
        else:
            st.write("Noch keine Zusagen.")

        if not is_preview:
            st.markdown("---")
            btn_label = "❌ Absagen" if is_attending else "✅ Zusage erteilen"
            if st.button(btn_label, key=f"join_{ev_id}"):
                if not has_user:
                    st.warning("⚠️ Bitte wähle oben dein Profil aus, um abzustimmen!")
                else:
                    if is_attending:
                        participants.remove(current_user)
                    else:
                        participants.append(current_user)

                    ev["participants"] = participants
                    if save_data(st.session_state["data"]):
                        st.rerun()

    # 3. EVENT DATEN DROPDOWN
    title_text = ev.get('title', 'Unbenanntes Event')
    with st.expander(f"📌 {title_text} - Details (Uhrzeit, Ort, Beschreibung)", expanded=False):
        st.write(f"📅 **Datum:** {ev.get('date', 'N/A')}")
        st.write(f"⏰ **Uhrzeit:** {ev.get('time', 'N/A')}")
        st.write(f"📍 **Ort:** {ev.get('location', 'N/A')}")
        if ev.get("description"):
            st.write(f"💬 **Beschreibung:** {ev.get('description')}")
        st.caption(f"Erstellt von: {ev.get('created_by', 'Anonym')}")

    # LÖSCHEN-KNOPF FÜR ADMIN DIRECT AM EVENT
    if is_admin and not is_preview:
        if st.button(f"🗑️ Event '{title_text}' löschen (Admin)", key=f"admin_del_direct_{ev_id}"):
            st.session_state["data"]["events"] = [e for e in st.session_state["data"]["events"] if e.get("id") != ev_id]
            if save_data(st.session_state["data"]):
                st.success("Event erfolgreich gelöscht.")
                st.rerun()


# ---------------------------------------------------------
# 5. SESSION STATE INITIALIZATION
# ---------------------------------------------------------
if "data" not in st.session_state:
    st.session_state["data"] = load_data()

data = st.session_state["data"]


# ---------------------------------------------------------
# 6. SIDEBAR
# ---------------------------------------------------------
expected_pin = st.secrets.get("ADMIN_PIN", "#together#")

with st.sidebar:
    st.header("⚙️ Optionen")
    admin_pin_input = st.text_input("🔑 Admin PIN", type="password", key="sidebar_admin_pin")
    is_admin = (admin_pin_input == expected_pin)

    if is_admin:
        st.success("Admin-Modus aktiv")
    elif admin_pin_input != "":
        st.error("Falsche PIN")


# ---------------------------------------------------------
# 7. HEADER & PROFILE SELECTION
# ---------------------------------------------------------
st.title("📅 Event Planner")

NO_USER_SELECTED = "-- Bitte wählen --"
user_list = data.get("users", [])
user_options = [NO_USER_SELECTED] + user_list

current_selection = st.session_state.get("current_user", NO_USER_SELECTED)
if current_selection not in user_options:
    current_selection = NO_USER_SELECTED

col_user_select, col_reload_btn = st.columns([3, 1])

with col_user_select:
    selected_user = st.selectbox(
        "👤 Profil wählen (zum Abstimmen):", 
        options=user_options, 
        index=user_options.index(current_selection)
    )
    st.session_state["current_user"] = selected_user

with col_reload_btn:
    st.write("")
    st.write("")
    if st.button("🔄 Drive", help="Daten neu aus Google Drive laden"):
        st.session_state["data"] = load_data()
        st.rerun()

st.markdown("---")


# ---------------------------------------------------------
# 8. NAVIGATION TABS
# ---------------------------------------------------------
tab_titles = ["📍 Aktuelle Woche", "🔮 Zukünftig", "📜 Vergangen", "➕ Neues Event"]
if is_admin:
    tab_titles.append("⚙️ Admin")

tabs = st.tabs(tab_titles)

tab_current = tabs[0]
tab_future = tabs[1]
tab_past = tabs[2]
tab_add = tabs[3]
tab_admin = tabs[4] if is_admin else None


# ---------------------------------------------------------
# HELPER: EVENT-LISTE RENDERN
# ---------------------------------------------------------
def render_event_list(event_list, empty_msg="Keine Events in diesem Bereich."):
    if not event_list:
        st.info(empty_msg)
        return

    sorted_events = sorted(event_list, key=lambda x: (x.get("date", ""), x.get("time", "")))

    grouped_events = {}
    for ev in sorted_events:
        d_str = ev.get("date", "")
        if d_str not in grouped_events:
            grouped_events[d_str] = []
        grouped_events[d_str].append(ev)

    for d_str, day_events in grouped_events.items():
        try:
            d_obj = datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
            formatted_date = format_german_date(d_obj)
        except Exception:
            formatted_date = d_str

        st.markdown(f'<div class="date-header">{formatted_date}</div>', unsafe_allow_html=True)

        for ev in day_events:
            render_single_event_card(ev, is_preview=False, is_admin=is_admin)


# EVENT KATEGORISIERUNG (Heute bis Heute + 7 Tage)
current_week_events = []
future_events = []
past_events = []

for event in data.get("events", []):
    d_str = event.get("date", "")
    try:
        ev_date = datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
        if today <= ev_date <= end_of_7_days:
            current_week_events.append(event)
        elif ev_date > end_of_7_days:
            future_events.append(event)
        else:
            past_events.append(event)
    except ValueError:
        future_events.append(event)


with tab_current:
    render_event_list(current_week_events, empty_msg="Keine Events in der aktuellen Woche.")

with tab_future:
    render_event_list(future_events, empty_msg="Keine zukünftigen Events nach dieser Woche.")

with tab_past:
    render_event_list(past_events, empty_msg="Keine vergangenen Events vorhanden.")


# ---------------------------------------------------------
# TAB 4: NEUES EVENT ERSTELLEN
# ---------------------------------------------------------
with tab_add:
    st.subheader("Event hinzufügen")
    
    if "add_success_msg" in st.session_state:
        st.success(st.session_state["add_success_msg"])
        del st.session_state["add_success_msg"]

    uploader_key = st.session_state.get("uploader_key", "flyer_uploader_0")

    # 1. KI-ANALYSE
    uploaded_flyer = st.file_uploader("Flyer-Bild auswählen & analysieren", type=["jpg", "jpeg", "png"], key=uploader_key)
    
    if uploaded_flyer and st.button("🪄 Flyer mit KI analysieren"):
        with st.spinner("Gemini analysiert das Flyer-Bild..."):
            file_bytes = uploaded_flyer.read()
            ai_data = analyze_flyer_with_gemini(file_bytes)
            if ai_data:
                img = PIL.Image.open(io.BytesIO(file_bytes))
                b64_img = image_to_base64(img)

                st.session_state["form_title"] = ai_data.get("title", "")
                st.session_state["form_date"] = ai_data.get("date", str(datetime.date.today()))
                st.session_state["form_time"] = ai_data.get("time", "19:00")
                st.session_state["form_location"] = ai_data.get("location", "")
                st.session_state["form_description"] = ai_data.get("description", "")
                st.session_state["form_flyer_b64"] = b64_img

                st.success("Daten und Bild aus dem Flyer extrahiert!")
                st.rerun()

    st.markdown("---")
    st.write("### Event-Daten eingeben")

    if st.session_state.get("form_flyer_b64"):
        st.write("**Übernommenes Flyer-Bild:**")
        try:
            prev_img = base64.b64decode(st.session_state["form_flyer_b64"])
            st.image(prev_img, width=250)
            if st.button("❌ Übernommenes Bild entfernen"):
                st.session_state.pop("form_flyer_b64", None)
                st.rerun()
        except Exception:
            pass

    # 2. MANUELLES FORMULAR
    with st.form("event_input_form", clear_on_submit=True):
        f_title = st.text_input("Titel*", value=st.session_state.get("form_title", ""))
        
        col_d, col_t = st.columns(2)
        with col_d:
            try:
                init_date = datetime.datetime.strptime(st.session_state.get("form_date", ""), "%Y-%m-%d").date()
            except Exception:
                init_date = datetime.date.today()
            f_date = st.date_input("Datum*", value=init_date)

        with col_t:
            f_time = st.text_input("Uhrzeit", value=st.session_state.get("form_time", "19:00"))

        f_loc = st.text_input("Ort / Location", value=st.session_state.get("form_location", ""))
        f_desc = st.text_area("Beschreibung", value=st.session_state.get("form_description", ""))
        f_file = st.file_uploader("Anderes Bild hochladen (optional)", type=["jpg", "jpeg", "png"])

        if st.form_submit_button("💾 Event speichern"):
            if not f_title:
                st.error("Bitte gib einen Titel ein.")
            elif is_duplicate_event(data, f_title, str(f_date)):
                st.error(f"⚠️ Ein Event mit dem Namen '{f_title}' existiert bereits am {f_date}!")
            else:
                b64_img = ""
                if f_file:
                    img = PIL.Image.open(f_file)
                    b64_img = image_to_base64(img)
                elif st.session_state.get("form_flyer_b64"):
                    b64_img = st.session_state.get("form_flyer_b64")

                creator = st.session_state.get("current_user", "Anonym")
                if creator == NO_USER_SELECTED:
                    creator = "Anonym"

                new_event = {
                    "id": str(datetime.datetime.now().timestamp()),
                    "title": f_title,
                    "date": str(f_date),
                    "time": f_time,
                    "location": f_loc,
                    "description": f_desc,
                    "created_by": creator,
                    "participants": [],
                    "flyer_b64": b64_img
                }

                data["events"].append(new_event)

                if save_data(data):
                    for key in ["form_title", "form_date", "form_time", "form_location", "form_description", "form_flyer_b64"]:
                        st.session_state.pop(key, None)

                    st.session_state["uploader_key"] = f"flyer_uploader_{datetime.datetime.now().timestamp()}"

                    target_tab_name = get_target_tab_name(f_date)
                    st.session_state["add_success_msg"] = f"✅ Event **'{f_title}'** wurde erfolgreich freigegeben und zum Reiter **'{target_tab_name}'** hinzugefügt."
                    
                    st.rerun()


# ---------------------------------------------------------
# TAB 5: ADMIN BEREICH
# ---------------------------------------------------------
if is_admin and tab_admin:
    with tab_admin:
        st.subheader("⚙️ Admin-Bereich")

        if "users" not in data or not isinstance(data["users"], list):
            data["users"] = ["Anna", "Julian", "Matthias"]

        st.markdown("---")
        st.write("### 👥 Nutzer verwalten")

        with st.expander("➕ Neuen Nutzer anlegen", expanded=False):
            new_user_name = st.text_input("Name des neuen Nutzers", key="add_user_input")
            if st.button("Nutzer anlegen", key="add_user_btn"):
                clean_name = new_user_name.strip()
                if clean_name:
                    if clean_name not in data["users"]:
                        data["users"].append(clean_name)
                        if save_data(data):
                            st.success(f"Nutzer '{clean_name}' hinzugefügt!")
                            st.rerun()
                    else:
                        st.warning(f"Nutzer '{clean_name}' existiert bereits.")
                else:
                    st.warning("Bitte gib einen Namen ein.")

        for user in data["users"]:
            col_name, col_rename_input, col_btn_rename, col_btn_del = st.columns([2, 2, 1, 1])

            with col_name:
                st.write(f"👤 **{user}**")

            with col_rename_input:
                new_name = st.text_input("Neuer Name", value=user, key=f"rename_input_{user}", label_visibility="collapsed")

            with col_btn_rename:
                if st.button("✏️", key=f"rename_btn_{user}", help=f"Nutzer '{user}' umbenennen"):
                    clean_new_name = new_name.strip()
                    if clean_new_name and clean_new_name not in data["users"]:
                        user_index = data["users"].index(user)
                        data["users"][user_index] = clean_new_name

                        for event in data.get("events", []):
                            if event.get("created_by") == user:
                                event["created_by"] = clean_new_name
                            if "participants" in event and user in event["participants"]:
                                event["participants"] = [clean_new_name if p == user else p for p in event["participants"]]

                        if st.session_state.get("current_user") == user:
                            st.session_state["current_user"] = clean_new_name

                        if save_data(data):
                            st.success(f"'{user}' umbenannt!")
                            st.rerun()

            with col_btn_del:
                if st.button("🗑️", key=f"delete_btn_{user}", help=f"Nutzer '{user}' löschen"):
                    if len(data["users"]) > 1:
                        data["users"].remove(user)
                        for event in data.get("events", []):
                            if "participants" in event and user in event["participants"]:
                                event["participants"].remove(user)
                        if save_data(data):
                            st.success(f"Nutzer '{user}' gelöscht!")
                            st.rerun()