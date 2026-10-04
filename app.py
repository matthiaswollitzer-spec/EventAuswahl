import base64
import datetime
import io
import json
import requests
import PIL.Image
import streamlit as st
from supabase import create_client, Client

# ---------------------------------------------------------
# 1. PAGE CONFIGURATION & CUSTOM CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title="Event-Planer",
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

    /* HAUPTÜBERSCHRIFT */
    .main-title {
        font-size: 1.6rem;
        font-weight: 700;
        margin-top: 5px;
        margin-bottom: 15px;
        display: flex;
        align-items: center;
        gap: 8px;
    }

    /* CLIENT-SIDE FLYER-ZOOM OHNE NEULADEN */
    .flyer-toggle {
        display: none;
    }
    .flyer-container .flyer-full {
        display: none;
    }
    .flyer-container .flyer-thumb {
        width: 120px;
        max-width: 100%;
        border-radius: 8px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);
        cursor: pointer;
        display: block;
        transition: transform 0.15s ease-in-out;
    }
    .flyer-container .flyer-hint {
        font-size: 0.78em;
        color: #666;
        font-weight: 500;
        display: block;
        margin-top: 4px;
        margin-bottom: 8px;
    }
    .flyer-toggle:checked + .flyer-container .flyer-thumb,
    .flyer-toggle:checked + .flyer-container .flyer-hint {
        display: none !important;
    }
    .flyer-toggle:checked + .flyer-container .flyer-full {
        display: block !important;
        width: 100%;
        max-width: 100%;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        cursor: pointer;
        margin-bottom: 12px;
    }
    </style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------
# 2. SUPABASE CONNECTION & DATABASE HELPERS
# ---------------------------------------------------------
@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()


def cleanup_old_events():
    """Löscht automatisch Events aus Supabase, deren Datum älter als 7 Tage ist (inklusive dazugehöriger Votes)."""
    try:
        cutoff_date = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        
        old_events = supabase.table("events").select("id").lt("date", cutoff_date).execute()
        if old_events.data:
            old_ids = [ev["id"] for ev in old_events.data]
            for ev_id in old_ids:
                supabase.table("votes").delete().eq("event_id", ev_id).execute()
            supabase.table("events").delete().in_("id", old_ids).execute()
    except Exception:
        pass


def load_users_db():
    """Lädt die Namensliste aus der Supabase 'users'-Tabelle."""
    default_names = ["Anna", "Ben", "Christian", "Daniela", "Julian", "Laura", "Max", "Sarah", "Stefan"]
    try:
        res = supabase.table("users").select("name").order("name").execute()
        if res.data and len(res.data) > 0:
            return [r["name"] for r in res.data if r.get("name")]
        else:
            for name in default_names:
                supabase.table("users").upsert({"name": name}).execute()
            return default_names
    except Exception:
        return default_names


def load_all_data():
    try:
        cleanup_old_events()

        events_res = supabase.table("events").select("*").execute()
        votes_res = supabase.table("votes").select("*").execute()
        
        events = events_res.data if events_res.data else []
        
        votes_map = {}
        for v in (votes_res.data if votes_res.data else []):
            ev_id = v["event_id"]
            if ev_id not in votes_map:
                votes_map[ev_id] = {}
            votes_map[ev_id][v["user_name"]] = v["vote_status"]
            
        return events, votes_map
    except Exception as e:
        st.error(f"Fehler beim Laden aus Supabase: {e}")
        return [], {}


def update_vote_status_db(ev_id, user_name, new_status):
    if not user_name or user_name.strip() == "" or user_name == "-- Bitte wählen --":
        st.warning("⚠️ Bitte wähle oben deinen Namen aus, um einzutragen!")
        return

    clean_user = user_name.strip()
    try:
        if new_status == "none":
            supabase.table("votes").delete().eq("event_id", ev_id).eq("user_name", clean_user).execute()
        else:
            supabase.table("votes").upsert(
                {
                    "event_id": ev_id,
                    "user_name": clean_user,
                    "vote_status": new_status,
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                },
                on_conflict="event_id,user_name"
            ).execute()
            
        st.rerun()
    except Exception as e:
        st.error(f"Fehler beim Speichern der Stimme: {e}")


def delete_event_completely(ev_id):
    """Löscht ein Event und alle zugehörigen Votes."""
    try:
        supabase.table("votes").delete().eq("event_id", ev_id).execute()
        supabase.table("events").delete().eq("id", ev_id).execute()
    except Exception as e:
        st.error(f"Fehler beim Löschen des Events: {e}")


# ---------------------------------------------------------
# 3. HELPER FUNCTIONS & GEMINI ANALYSIS
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
        # Aktualisiert auf das korrekte Gemini Modell gemäß Google Vorgaben
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

        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        
        if res.status_code == 200:
            text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        elif res.status_code in [429, 500, 503]:
            st.warning("⚠️ Die KI ist gerade überlastet. Bitte erneut versuchen.")
            return None
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


def is_duplicate_event(events, title, date_str, current_event_id=None):
    clean_title = title.strip().lower()
    clean_date = date_str.strip()

    for ev in events:
        if current_event_id and ev.get("id") == current_event_id:
            continue
        if ev.get("title", "").strip().lower() == clean_title and ev.get("date", "").strip() == clean_date:
            return True
    return False


def prepare_edit_event(ev, flyer_data):
    st.session_state["edit_event_id"] = ev.get("id")
    st.session_state["form_title"] = ev.get("title", "")
    st.session_state["form_date"] = ev.get("date", str(datetime.date.today()))
    st.session_state["form_time"] = ev.get("time", "19:00")
    st.session_state["form_location"] = ev.get("location", "")
    st.session_state["form_description"] = ev.get("description", "")
    if flyer_data:
        st.session_state["form_flyer_b64"] = flyer_data
    else:
        st.session_state.pop("form_flyer_b64", None)
    st.session_state["active_tab"] = "➕ Neues Event"


def cancel_edit_mode():
    for key in ["edit_event_id", "form_title", "form_date", "form_time", "form_location", "form_description", "form_flyer_b64", "last_uploaded_bytes", "last_uploaded_name"]:
        st.session_state.pop(key, None)
    st.session_state["active_tab"] = "📍 Aktuelle Woche"


# ---------------------------------------------------------
# 4. SINGLE EVENT CARD RENDERER
# ---------------------------------------------------------
def render_single_event_card(ev, votes_dict, is_admin=False):
    ev_id = ev.get("id")

    flyer_data = ev.get("flyer_b64")
    if flyer_data:
        try:
            if "," in flyer_data:
                flyer_data = flyer_data.split(",")[1]

            st.markdown(
                f"""
                <div style="margin-bottom: 12px;">
                    <input type="checkbox" id="flyer-zoom-{ev_id}" class="flyer-toggle">
                    <label for="flyer-zoom-{ev_id}" class="flyer-container" style="display: block; cursor: pointer;">
                        <img src="data:image/jpeg;base64,{flyer_data}" class="flyer-thumb">
                        <span class="flyer-hint">🔍 Flyer tippen zum Vergrößern</span>
                        <img src="data:image/jpeg;base64,{flyer_data}" class="flyer-full">
                    </label>
                </div>
                """,
                unsafe_allow_html=True
            )
        except Exception:
            st.caption("⚠️ Bild konnte nicht angezeigt werden.")

    event_votes = votes_dict.get(ev_id, {})
    
    yes_list = sorted([user for user, status in event_votes.items() if status == "yes"], key=str.lower)
    maybe_list = sorted([user for user, status in event_votes.items() if status == "maybe"], key=str.lower)

    current_user = st.session_state.get("current_user", "").strip()
    user_status = event_votes.get(current_user, "none") if current_user else "none"

    vote_count = len(yes_list) + len(maybe_list)
    has_votes = vote_count > 0

    if has_votes:
        expander_title = f"🟢 Rückmeldungen: {len(yes_list)} Zusagen"
        if maybe_list:
            expander_title += f", {len(maybe_list)} Vielleicht"
    else:
        expander_title = f"👥 Rückmeldungen: 0 Rückmeldungen"

    with st.expander(expander_title, expanded=False):
        col_yes, col_maybe = st.columns(2)
        with col_yes:
            st.write("**✅ Dabeisein:**")
            if yes_list:
                for p in yes_list:
                    st.write(f"- {p}")
            else:
                st.caption("Keine Zusagen")

        with col_maybe:
            st.write("**❓ Vielleicht:**")
            if maybe_list:
                for p in maybe_list:
                    st.write(f"- {p}")
            else:
                st.caption("Keine Unsicheren")

        st.markdown("---")
        st.write("**Deine Rückmeldung:**")
        
        btn_col1, btn_col2, btn_col3 = st.columns(3)
        
        with btn_col1:
            btn_yes_type = "primary" if user_status == "yes" else "secondary"
            if st.button("✅ Zusage", key=f"vote_yes_{ev_id}", type=btn_yes_type):
                update_vote_status_db(ev_id, current_user, "yes")

        with btn_col2:
            btn_maybe_type = "primary" if user_status == "maybe" else "secondary"
            if st.button("❓ Vielleicht", key=f"vote_maybe_{ev_id}", type=btn_maybe_type):
                update_vote_status_db(ev_id, current_user, "maybe")

        with btn_col3:
            if st.button("❌ Absagen", key=f"vote_no_{ev_id}"):
                update_vote_status_db(ev_id, current_user, "none")

    title_text = ev.get('title', 'Unbenanntes Event')
    details_title = f"📌 {title_text} - Details (Uhrzeit, Ort, Beschreibung)"
    
    with st.expander(details_title, expanded=False):
        st.write(f"📅 **Datum:** {ev.get('date', 'N/A')}")
        st.write(f"⏰ **Uhrzeit:** {ev.get('time', 'N/A')}")
        st.write(f"📍 **Ort:** {ev.get('location', 'N/A')}")
        if ev.get("description"):
            st.write(f"💬 **Beschreibung:** {ev.get('description')}")
        st.caption(f"Erstellt von: {ev.get('created_by', 'Anonym')}")

        st.markdown("---")
        st.button(
            "✏️ Event anpassen",
            key=f"edit_event_btn_{ev_id}",
            on_click=prepare_edit_event,
            args=(ev, flyer_data)
        )

    if is_admin:
        if st.button(f"🗑️ Event '{title_text}' löschen (Admin)", key=f"admin_del_direct_{ev_id}"):
            delete_event_completely(ev_id)
            st.success("Event und alle dazugehörigen Votes erfolgreich gelöscht.")
            st.rerun()


# ---------------------------------------------------------
# 5. INITIALISIERUNG & DATEN LADEN
# ---------------------------------------------------------
events_data, votes_map = load_all_data()
db_users = load_users_db()


# ---------------------------------------------------------
# 6. SIDEBAR & REIHENFOLGE: TITEL -> USER-AUSWAHL -> REITER
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

st.markdown('<div class="main-title">📅 Event-Planer</div>', unsafe_allow_html=True)

USER_NAMES = ["-- Bitte wählen --"] + db_users

if "current_user" not in st.session_state or st.session_state["current_user"] not in USER_NAMES:
    st.session_state["current_user"] = USER_NAMES[0]

selected_user = st.selectbox(
    "👤 Dein Name (für Abstimmungen):",
    options=USER_NAMES,
    index=USER_NAMES.index(st.session_state["current_user"])
)

st.session_state["current_user"] = "" if selected_user == "-- Bitte wählen --" else selected_user

if "add_success_msg" in st.session_state:
    st.success(st.session_state["add_success_msg"])
    del st.session_state["add_success_msg"]

st.markdown("---")


# ---------------------------------------------------------
# 7. INTERAKTIVE REITERLEISTE (SEGMENTED CONTROL)
# ---------------------------------------------------------
tab_titles = ["📍 Aktuelle Woche", "🔮 Zukünftig", "📜 Vergangen", "➕ Neues Event"]
if is_admin:
    tab_titles.append("⚙️ Admin")

if "next_tab" in st.session_state:
    st.session_state["active_tab"] = st.session_state.pop("next_tab")

if "active_tab" not in st.session_state or st.session_state["active_tab"] not in tab_titles:
    st.session_state["active_tab"] = tab_titles[0]

selected_tab = st.segmented_control(
    "Navigation",
    options=tab_titles,
    key="active_tab",
    label_visibility="collapsed"
)


# ---------------------------------------------------------
# 8. EVENTS SORTIEREN & FILTERN
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
            render_single_event_card(ev, votes_map, is_admin=is_admin)


current_week_events = []
future_events = []
past_events = []

for event in events_data:
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


# ---------------------------------------------------------
# 9. INHALTE DER EINZELNEN TABS RENDERN
# ---------------------------------------------------------
if selected_tab == "📍 Aktuelle Woche":
    render_event_list(current_week_events, empty_msg="Keine Events in der aktuellen Woche.")

elif selected_tab == "🔮 Zukünftig":
    render_event_list(future_events, empty_msg="Keine zukünftigen Events nach dieser Woche.")

elif selected_tab == "📜 Vergangen":
    render_event_list(past_events, empty_msg="Keine vergangenen Events vorhanden.")

elif selected_tab == "➕ Neues Event":
    is_editing = "edit_event_id" in st.session_state
    
    if is_editing:
        col_heading, col_cancel = st.columns([3, 1])
        with col_heading:
            st.subheader("✏️ Event bearbeiten / anpassen")
        with col_cancel:
            st.button("❌ Abbrechen", key="cancel_edit_mode", on_click=cancel_edit_mode)
    else:
        st.subheader("Event hinzufügen")

    has_image = bool(st.session_state.get("form_flyer_b64"))

    if not has_image:
        uploaded_flyer = st.file_uploader(
            "Flyer / Event-Bild auswählen (wird sofort automatisch analysiert)", 
            type=["jpg", "jpeg", "png"], 
            key="static_flyer_uploader"
        )
        
        if uploaded_flyer is not None:
            if st.session_state.get("last_uploaded_name") != uploaded_flyer.name:
                file_bytes = uploaded_flyer.read()
                st.session_state["last_uploaded_name"] = uploaded_flyer.name
                st.session_state["last_uploaded_bytes"] = file_bytes
                
                try:
                    img = PIL.Image.open(io.BytesIO(file_bytes))
                    b64_img = image_to_base64(img)
                    st.session_state["form_flyer_b64"] = b64_img
                except Exception as e:
                    st.error(f"Fehler beim Laden des Bildes: {e}")

                with st.spinner("🤖 Gemini analysiert den Flyer automatisch..."):
                    ai_data = analyze_flyer_with_gemini(file_bytes)
                    if ai_data:
                        if ai_data.get("title"):
                            st.session_state["form_title"] = ai_data["title"]
                        if ai_data.get("date"):
                            st.session_state["form_date"] = ai_data["date"]
                        if ai_data.get("time"):
                            st.session_state["form_time"] = ai_data["time"]
                        if ai_data.get("location"):
                            st.session_state["form_location"] = ai_data["location"]
                        if ai_data.get("description"):
                            st.session_state["form_description"] = ai_data["description"]
                st.rerun()

    if st.session_state.get("form_flyer_b64"):
        st.write("**Vorschau des Event-Bildes:**")
        try:
            prev_img = base64.b64decode(st.session_state["form_flyer_b64"])
            st.image(prev_img, width=300)
            
            if st.button("🗑️ Bild entfernen", key="btn_remove_flyer_img"):
                st.session_state.pop("form_flyer_b64", None)
                st.session_state.pop("last_uploaded_bytes", None)
                st.session_state.pop("last_uploaded_name", None)
                st.rerun()
        except Exception:
            st.caption("⚠ Bild konnte nicht angezeigt werden.")

    st.markdown("---")
    st.write("### Event-Daten")

    with st.form("event_input_form", clear_on_submit=False):
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

        submit_btn_label = "🔄 Event aktualisieren" if is_editing else "💾 Event speichern"

        if st.form_submit_button(submit_btn_label):
            edit_id = st.session_state.get("edit_event_id")

            if not f_title:
                st.error("Bitte gib einen Titel ein.")
            elif is_duplicate_event(events_data, f_title, str(f_date), current_event_id=edit_id):
                st.error(f"⚠️ Ein Event mit dem Namen '{f_title}' existiert bereits am {f_date}!")
            else:
                b64_img = st.session_state.get("form_flyer_b64", "")
                creator = st.session_state.get("current_user") or "Anonym"
                target_tab_name = get_target_tab_name(f_date)

                if is_editing and edit_id:
                    supabase.table("events").update({
                        "title": f_title,
                        "date": str(f_date),
                        "time": f_time,
                        "location": f_loc,
                        "description": f_desc,
                        "flyer_b64": b64_img
                    }).eq("id", edit_id).execute()
                    st.session_state["add_success_msg"] = f"✅ Event **'{f_title}'** wurde aktualisiert!"
                else:
                    new_event_id = str(datetime.datetime.now().timestamp())
                    supabase.table("events").insert({
                        "id": new_event_id,
                        "title": f_title,
                        "date": str(f_date),
                        "time": f_time,
                        "location": f_loc,
                        "description": f_desc,
                        "created_by": creator,
                        "flyer_b64": b64_img
                    }).execute()
                    st.session_state["add_success_msg"] = f"✅ Event **'{f_title}'** wurde erstellt!"

                for key in ["edit_event_id", "form_title", "form_date", "form_time", "form_location", "form_description", "form_flyer_b64", "last_uploaded_bytes", "last_uploaded_name"]:
                    st.session_state.pop(key, None)

                st.session_state["next_tab"] = target_tab_name
                st.rerun()

# HIER WAR DER FEHLER: Korrigiert auf "⚙️ Admin" (mit Emoji wie in tab_titles)
elif selected_tab == "⚙️ Admin" and is_admin:
    st.subheader("⚙️ Admin-Verwaltung")
    
    st.markdown("### 👤 Namen im Dropdown verwalten")
    col_add, col_edit_user, col_del = st.columns(3)
    
    with col_add:
        st.write("**Hinzufügen:**")
        new_name_input = st.text_input("Neuer Name:", key="input_new_admin_user")
        if st.button("➕ Hinzufügen", key="btn_add_user"):
            clean_new_name = new_name_input.strip()
            if clean_new_name:
                try:
                    supabase.table("users").upsert({"name": clean_new_name}).execute()
                    st.success(f"Name '{clean_new_name}' hinzugefügt!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Fehler: {e}")
            else:
                st.warning("Bitte Namen eingeben.")

    with col_edit_user:
        st.write("**Umbenennen:**")
        removable_users = [u for u in db_users if u != "-- Bitte wählen --"]
        if removable_users:
            user_to_rename = st.selectbox("Auswählen:", options=removable_users, key="select_rename_admin_user")
            new_renamed_input = st.text_input("Neuer Name:", key="input_renamed_user")
            if st.button("✏️ Umbenennen", key="btn_rename_user"):
                clean_new_name = new_renamed_input.strip()
                if clean_new_name and clean_new_name != user_to_rename:
                    try:
                        supabase.table("users").upsert({"name": clean_new_name}).execute()
                        supabase.table("votes").update({"user_name": clean_new_name}).eq("user_name", user_to_rename).execute()
                        supabase.table("users").delete().eq("name", user_to_rename).execute()
                        
                        if st.session_state.get("current_user") == user_to_rename:
                            st.session_state["current_user"] = clean_new_name
                            
                        st.success(f"'{user_to_rename}' wurde in '{clean_new_name}' umbenannt (inkl. Votes)!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fehler beim Umbenennen: {e}")
                else:
                    st.warning("Bitte einen anderen, gültigen neuen Namen eingeben.")
        else:
            st.caption("Keine Namen verfügbar.")

    with col_del:
        st.write("**Entfernen:**")
        if removable_users:
            user_to_delete = st.selectbox("Auswählen:", options=removable_users, key="select_del_admin_user")
            if st.button("🗑️ Löschen", key="btn_del_user"):
                try:
                    supabase.table("votes").delete().eq("user_name", user_to_delete).execute()
                    supabase.table("users").delete().eq("name", user_to_delete).execute()
                    
                    if st.session_state.get("current_user") == user_to_delete:
                        st.session_state["current_user"] = ""
                        
                    st.success(f"Name '{user_to_delete}' und all seine Votes wurden entfernt!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Fehler beim Entfernen: {e}")
        else:
            st.caption("Keine Namen vorhanden.")