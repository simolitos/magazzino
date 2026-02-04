import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, timedelta
import math
import io
import json
from fpdf import FPDF
import time

# --- CONFIGURAZIONE ---
st.set_page_config(page_title="VIRTUAL Magazzino", layout="wide", initial_sidebar_state="expanded")

# --- STILE CSS (Titoli + LOADING SPINNER) ---
st.markdown("""
    <style>
    /* Titolo Principale */
    .title-text {
        font-size: 38px;
        font-weight: 800;
        color: #1E3A8A;
        margin-bottom: 0px;
    }
    .credits {
        font-size: 14px;
        font-style: italic;
        color: #64748B;
        vertical-align: middle;
        margin-left: 10px;
    }
    .block-container {
        padding-top: 2rem;
    }
    
    /* --- CUSTOM LOADER (Overlay) --- */
    .stSpinner { display: none; } /* Nascondi spinner default */
    
    #custom-loader {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background-color: rgba(255, 255, 255, 0.85);
        backdrop-filter: blur(5px);
        z-index: 999999;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }
    
    .spinner {
        width: 50px;
        height: 50px;
        border: 5px solid #f3f3f3;
        border-top: 5px solid #1E3A8A;
        border-radius: 50%;
        animation: spin 1s linear infinite;
        margin-bottom: 15px;
    }
    
    .loading-text {
        font-family: 'Arial', sans-serif;
        font-size: 18px;
        font-weight: 600;
        color: #1E3A8A;
        animation: pulse 1.5s infinite;
    }
    
    @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }
    
    @keyframes pulse {
        0% { opacity: 0.6; }
        50% { opacity: 1; }
        100% { opacity: 0.6; }
    }
    </style>
    """, unsafe_allow_html=True)

# --- PARAMETRI DI CALCOLO ---
MESI_COPERTURA = 1.0      
MESI_BUFFER = 0.25        # 1 Settimana
TARGET_MESI = MESI_COPERTURA + MESI_BUFFER 
MIN_SCORTA_CAL = 3        

# --- CONNESSIONE GOOGLE SHEETS ---
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("⚠️ Errore Segreti: Configura .streamlit/secrets.toml")
    st.stop()

# --- CARICAMENTO DATI ---
@st.cache_data
def load_master_data():
    try:
        df = pd.read_excel('dati.xlsx', engine='openpyxl')
        
        if 'LN ABBOTT' in df.columns and 'LN ABBOTT AGGIORNATI' in df.columns:
            df['Codice_Finale'] = df['LN ABBOTT'].fillna(df['LN ABBOTT AGGIORNATI'])
        else:
            df['Codice_Finale'] = df.iloc[:, 4] 

        col_map = {
            'Codice_Finale': 'Codice',
            'Descrizione commerciale': 'Descrizione',
            'Rgt/Cal/QC/Cons': 'Categoria',
            '# Kit/Mese': 'Fabbisogno_Kit_Mese_Stimato', 
            'Test TOT MEDI/MESE Aggiustati': 'Test_Mensili_Reali',
            'KIT': 'Test_per_Scatola',
            'Conf.to': 'Confezione',
            'Assay name': 'Assay_Name'
        }
        
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        
        df = df[df['Descrizione'].notna() & df['Codice'].notna()] 
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False)
        df['Categoria'] = df['Categoria'].astype(str).fillna('')
        df['Assay_Name'] = df['Assay_Name'].astype(str).fillna('')
        
        # --- GESTIONE VALORI SPECIALI ---
        def clean_custom_values(val):
            if pd.isna(val): return val
            s = str(val).strip()
            
            # REGOLE UTENTE
            if "25-30" in s: return 30        # GLP SCREWCAPS
            if "28" in s and "?" in s: return 4   # ACID PROBE WASH (Era 28???, ora forzato a 4)
            if "12/15" in s: return 15        # PRE-TRIGGER
            
            return val

        df['Fabbisogno_Kit_Mese_Stimato'] = df['Fabbisogno_Kit_Mese_Stimato'].apply(clean_custom_values)
        df['Kit_Mese_Numeric'] = pd.to_numeric(df['Fabbisogno_Kit_Mese_Stimato'], errors='coerce')
        
        has_valid_consumption = df['Kit_Mese_Numeric'].notna()
        is_calibrator = df['Categoria'].str.upper().str.contains("CAL")
        is_homocysteine = df['Codice'].str.contains("09P2820", case=False)
        
        df = df[has_valid_consumption | is_calibrator | is_homocysteine]
        
        for col in ['Test_Mensili_Reali', 'Test_per_Scatola']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0
        
        df.loc[df['Codice'].str.contains("09P2820", case=False), 'Test_Mensili_Reali'] = 1000

        return df
    except Exception as e:
        st.error(f"Errore Excel: {e}")
        return pd.DataFrame()

# --- FUNZIONI CLOUD ---
def fetch_inventory():
    try:
        df_db = conn.read(worksheet="Foglio1", ttl=0)
        magazzino = {}
        if not df_db.empty and 'Codice' in df_db.columns:
            df_db['Codice'] = df_db['Codice'].astype(str)
            for _, row in df_db.iterrows():
                cod = str(row['Codice'])
                qty = row['Quantita']
                try: scadenze = json.loads(row['Scadenze_JSON'])
                except: scadenze = []
                magazzino[cod] = {'qty': qty, 'scadenze': scadenze}
        return magazzino
    except: return {}

def update_inventory(magazzino_dict):
    data_list = []
    for cod, info in magazzino_dict.items():
        if info['qty'] > 0: 
            data_list.append({
                "Codice": cod,
                "Quantita": info['qty'],
                "Scadenze_JSON": json.dumps(info['scadenze']),
                "Ultima_Modifica": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
    
    if not data_list:
        df_new = pd.DataFrame(columns=["Codice", "Quantita", "Scadenze_JSON", "Ultima_Modifica"])
    else:
        df_new = pd.DataFrame(data_list)
        
    conn.update(worksheet="Foglio1", data=df_new)

def manage_log_cloud(azione, prodotto_nome, qta):
    try:
        try: df_log = conn.read(worksheet="Logs", ttl=0)
        except: df_log = pd.DataFrame(columns=["Timestamp", "Data_Leggibile", "Azione", "Prodotto"])

        now = datetime.now()
        new_row = {
            "Timestamp": now,
            "Data_Leggibile": now.strftime("%d/%m %H:%M"),
            "Azione": f"{azione} ({qta})",
            "Prodotto": prodotto_nome
        }
        
        df_log = pd.concat([pd.DataFrame([new_row]), df_log], ignore_index=True)
        
        sette_giorni_fa = now - timedelta(days=7)
        df_log['Timestamp'] = pd.to_datetime(df_log['Timestamp'])
        df_log_clean = df_log[df_log['Timestamp'] > sette_giorni_fa]
        
        conn.update(worksheet="Logs", data=df_log_clean)
        return df_log_clean
    except Exception as e:
        return pd.DataFrame()

def fetch_only_log():
    try:
        df_log = conn.read(worksheet="Logs", ttl=0)
        if not df_log.empty:
            df_log['Timestamp'] = pd.to_datetime(df_log['Timestamp'])
            df_log = df_log.sort_values(by='Timestamp', ascending=False)
        return df_log
    except: return pd.DataFrame()

class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, f'Inventario Magazzino - {datetime.now().strftime("%d/%m/%Y")}', 0, 1, 'C')
        self.ln(5)
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')

def create_pdf_report(df_data):
    pdf = PDF()
    pdf.add_page()
    pdf.set_font('Arial', '', 10)
    categorie = sorted(df_data['Categoria'].unique().astype(str))
    for cat in categorie:
        pdf.set_fill_color(200, 220, 255)
        pdf.set_font('Arial', 'B', 12)
        cat_clean = cat.encode('latin-1', 'replace').decode('latin-1')
        pdf.cell(0, 10, f"CATEGORIA: {cat_clean}", 1, 1, 'L', fill=True)
        
        subset = df_data[df_data['Categoria'] == cat].sort_values(by='Descrizione')
        
        pdf.set_font('Arial', 'B', 9)
        pdf.cell(30, 8, "Codice", 1)
        pdf.cell(130, 8, "Prodotto", 1)
        pdf.cell(30, 8, "Giacenza", 1)
        pdf.ln()
        
        pdf.set_font('Arial', '', 9)
        for _, row in subset.iterrows():
            nome = str(row['Descrizione'])[:75].encode('latin-1', 'replace').decode('latin-1')
            cod = str(row['Codice']).encode('latin-1', 'replace').decode('latin-1')
            qta = str(int(row['Giacenza']))
            pdf.cell(30, 7, cod, 1)
            pdf.cell(130, 7, nome, 1)
            pdf.cell(30, 7, qta, 1)
            pdf.ln()
        pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- APP HEADER ---
st.markdown("""
    <div>
        <span class='title-text'>🌐 VirtuaL: Magazzino Abbott-LT</span>
        <span class='credits'>@SimoneR</span>
    </div>
    """, unsafe_allow_html=True)
st.divider()

# --- SIDEBAR ---
with st.sidebar:
    st.header("🖨️ STAMPA")
    if st.button("📄 Genera PDF Giacenza"):
        df_m = load_master_data()
        if 'magazzino' in st.session_state:
            df_print = df_m.copy()
            df_print['Giacenza'] = df_print['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
            df_print = df_print[df_print['Giacenza'] > 0]
            if not df_print.empty:
                pdf_bytes = create_pdf_report(df_print)
                st.download_button("📥 Scarica PDF", data=pdf_bytes, file_name=f"inventario_{datetime.now().strftime('%Y%m%d')}.pdf", mime="application/pdf")
            else: st.warning("Magazzino vuoto!")

    st.divider()
    st.header("📋 LOG (Ultimi 7gg)")
    
    if 'cloud_log' not in st.session_state:
        st.session_state['cloud_log'] = fetch_only_log()
    
    if st.button("🔄 Aggiorna Log"):
        st.session_state['cloud_log'] = fetch_only_log()
        st.rerun()

    if not st.session_state['cloud_log'].empty:
        show_log = st.session_state['cloud_log'][['Data_Leggibile', 'Azione', 'Prodotto']].head(50)
        st.dataframe(show_log, hide_index=True, use_container_width=True)
    else:
        st.caption("Nessun evento recente.")

df_master = load_master_data()

# Init Cloud
if 'magazzino' not in st.session_state:
    with st.spinner("⏳ Sincronizzazione Cloud..."):
        st.session_state['magazzino'] = fetch_inventory()

if not df_master.empty:
    
    tab_mov, tab_ordini, tab_scadenze = st.tabs(["⚡ OPERAZIONI", "🛒 ORDINI & ANALISI", "🗓️ SCADENZE"])

    # === TAB 1: OPERAZIONI ===
    with tab_mov:
        col_sel, col_dati = st.columns([3, 1])
        with col_sel:
            def get_label(row):
                c = str(row['Codice'])
                g = st.session_state['magazzino'].get(c, {}).get('qty', 0)
                assay = str(row['Assay_Name'])
                assay_str = f" ({assay})" if assay and assay != 'nan' else ""
                return f"{row['Descrizione']}{assay_str} (Disp: {g})"
            
            opzioni = df_master.apply(get_label, axis=1).tolist()
            scelta = st.selectbox("Cerca Prodotto (Nome, Codice, Assay):", opzioni)
            
            # Match
            df_master['Menu_Label'] = df_master.apply(get_label, axis=1)
            row_art = df_master[df_master['Menu_Label'] == scelta].iloc[0]
            codice = str(row_art['Codice'])
            
        with col_dati:
            giacenza_attuale = st.session_state['magazzino'].get(codice, {}).get('qty', 0)
            st.metric("Giacenza Attuale", f"{int(giacenza_attuale)}", delta="scatole")
            if "CAL" in str(row_art['Categoria']).upper():
                st.warning("⚠️ Calibratore")

        # PANNELLO DI CONTROLLO
        with st.container(border=True):
            st.subheader("🛠️ Pannello Azioni")
            c1, c2, c3 = st.columns([1, 2, 1])
            with c1:
                qty_input = st.number_input("Quantità", min_value=1, value=1, step=1)
            with c2:
                azione = st.radio("Seleziona Azione:", ["➖ PRELIEVO", "➕ CARICO", "🔧 RETTIFICA (=)"], horizontal=True)
            
            scad_display, scad_sort = "-", None
            if "CARICO" in azione:
                with c3:
                    cm, ca = st.columns(2)
                    mm = cm.selectbox("Mese", range(1, 13))
                    yy = ca.selectbox("Anno", range(datetime.now().year, datetime.now().year + 6))
                    scad_display = f"{mm:02d}/{yy}"
                    scad_sort = f"{yy}-{mm:02d}"

            # PULSANTE ESEGUI CON LOADER
            if st.button("🚀 ESEGUI OPERAZIONE", type="primary", use_container_width=True):
                # 1. LOADER HTML
                loader_placeholder = st.empty()
                loader_placeholder.markdown("""
                    <div id="custom-loader">
                        <div class="spinner"></div>
                        <div class="loading-text">Salvataggio in Cloud...</div>
                    </div>
                """, unsafe_allow_html=True)
                
                # 2. LOGICA
                if codice not in st.session_state['magazzino']:
                    st.session_state['magazzino'][codice] = {'qty': 0, 'scadenze': []}
                
                ref = st.session_state['magazzino'][codice]
                tipo_azione_log = ""
                err = False

                if "CARICO" in azione:
                    ref['qty'] += qty_input
                    ref['scadenze'].append({'display': scad_display, 'sort': scad_sort, 'qty': qty_input})
                    ref['scadenze'].sort(key=lambda x: x['sort'])
                    tipo_azione_log = "Carico"

                elif "PRELIEVO" in azione:
                    if ref['qty'] < qty_input:
                        err = True
                    else:
                        ref['qty'] -= qty_input
                        rem = qty_input
                        new_scad = []
                        for batch in ref['scadenze']:
                            if rem > 0:
                                if batch['qty'] > rem:
                                    batch['qty'] -= rem
                                    rem = 0
                                    new_scad.append(batch)
                                else:
                                    rem -= batch['qty']
                            else:
                                new_scad.append(batch)
                        ref['scadenze'] = new_scad
                        tipo_azione_log = "Prelievo"

                elif "RETTIFICA" in azione:
                    diff = qty_input - ref['qty']
                    if diff == 0: err = True
                    else:
                        ref['qty'] = qty_input
                        if diff > 0: ref['scadenze'].append({'display': 'MANUALE', 'sort': '9999-12', 'qty': diff})
                        else:
                            da_togliere = abs(diff)
                            new_scad = []
                            for batch in ref['scadenze']:
                                if da_togliere > 0:
                                    if batch['qty'] > da_togliere:
                                        batch['qty'] -= da_togliere
                                        da_togliere = 0
                                        new_scad.append(batch)
                                    else:
                                        da_togliere -= batch['qty']
                                else:
                                    new_scad.append(batch)
                            ref['scadenze'] = new_scad
                        tipo_azione_log = "Rettifica"

                # 3. FINE
                if err:
                    loader_placeholder.empty()
                    if "PRELIEVO" in azione: st.error("Quantità insufficiente!")
                    else: st.warning("Nessuna modifica.")
                else:
                    update_inventory(st.session_state['magazzino'])
                    st.session_state['cloud_log'] = manage_log_cloud(
                        tipo_azione_log, 
                        row_art['Descrizione'], 
                        qty_input if "RETTIFICA" not in azione else f"-> {qty_input}"
                    )
                    loader_placeholder.empty()
                    st.toast(f"✅ Salvato: {azione} eseguita!", icon="☁️")
                    time.sleep(1) 
                    st.rerun()

    # === TAB 2: ORDINI ===
    with tab_ordini:
        st.markdown("### 🚦 Analisi Fabbisogno (1.25 Mesi)")
        c_search, c_filtro = st.columns([2,1])
        term = c_search.text_input("🔍 Cerca (Nome, Codice, Assay)...", placeholder="Scrivi qui...")
        filtro = c_filtro.multiselect("Filtra Stato:", ["🔴 SOTTO MINIMO", "🔴 ESAURITO", "🟡 DA ORDINARE", "🟢 OK"], default=["🔴 SOTTO MINIMO", "🔴 ESAURITO", "🟡 DA ORDINARE"])
        
        df_c = df_master.copy()
        df_c['Giacenza'] = df_c['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
        
        def calcola_stato(row):
            consumo = 0
            if row['Test_Mensili_Reali'] > 0 and row['Test_per_Scatola'] > 0:
                consumo = row['Test_Mensili_Reali'] / row['Test_per_Scatola']
            elif row['Kit_Mese_Numeric'] > 0:
                consumo = row['Kit_Mese_Numeric']
            
            target = math.ceil(consumo * TARGET_MESI)
            is_cal = "CAL" in str(row['Categoria']).upper()
            if is_cal: target = max(target, MIN_SCORTA_CAL)
            
            da_ord = max(0, target - row['Giacenza'])
            
            # Calcolo Giorni Copertura
            copertura_giorni = None
            if consumo > 0 and row['Giacenza'] > 0:
                consumo_giornaliero = consumo / 30
                days = int(row['Giacenza'] / consumo_giornaliero)
                copertura_giorni = days
            
            stato = "🟢 OK"
            if is_cal and row['Giacenza'] < MIN_SCORTA_CAL: stato = "🔴 SOTTO MINIMO"
            elif row['Giacenza'] == 0: stato = "🔴 ESAURITO"
            elif da_ord > 0: stato = "🟡 DA ORDINARE"
            
            return pd.Series([stato, target, da_ord, copertura_giorni])

        df_c[['Stato', 'Target', 'Da_Ordinare', 'Days_Left']] = df_c.apply(calcola_stato, axis=1)
        
        df_view = df_c.copy()
        if filtro: df_view = df_view[df_view['Stato'].isin(filtro)]
        if term: 
            df_view = df_view[
                df_view['Descrizione'].str.contains(term, case=False, na=False) | 
                df_view['Codice'].str.contains(term, case=False, na=False) |
                df_view['Categoria'].str.contains(term, case=False, na=False) |
                df_view['Assay_Name'].str.contains(term, case=False, na=False)
            ]
        df_view = df_view.sort_values(by=['Da_Ordinare'], ascending=False)
        
        st.dataframe(
            df_view[['Stato', 'Categoria', 'Assay_Name', 'Codice', 'Descrizione', 'Giacenza', 'Target', 'Days_Left', 'Da_Ordinare']],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Stato": st.column_config.TextColumn("Stato", width="small"),
                "Categoria": st.column_config.TextColumn("Tipo", width="small"),
                "Assay_Name": st.column_config.TextColumn("Assay", width="medium"),
                "Codice": st.column_config.TextColumn("LN Abbott", width="medium"),
                "Descrizione": st.column_config.TextColumn("Prodotto", width="large"),
                "Target": st.column_config.NumberColumn("Target"),
                "Days_Left": st.column_config.NumberColumn("Copertura", format="%d gg", help="Giorni di autonomia stimati"),
                "Da_Ordinare": st.column_config.NumberColumn("🛒 ORDINA")
            }
        )
        
        st.divider()
        st.write("### 📤 Esporta per Fornitore")
        df_export = df_c[df_c['Da_Ordinare'] > 0].copy()
        df_export = df_export[['Codice', 'Descrizione', 'Da_Ordinare', 'Confezione']]
        df_export = df_export.rename(columns={'Codice': 'Codice Prodotto', 'Da_Ordinare': 'Qta Ordine', 'Confezione': 'Conf.to'})
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_export.to_excel(writer, index=False)
            
        st.download_button("📥 Scarica Ordine (Excel)", data=buffer.getvalue(), file_name=f"ordine_abbott_{datetime.now().strftime('%Y-%m-%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")

    # === TAB 3: SCADENZE ===
    with tab_scadenze:
        scad_list = []
        today = datetime.now().strftime("%Y-%m")
        limit = (datetime.now() + pd.DateOffset(months=3)).strftime("%Y-%m")
        
        for cod, data in st.session_state['magazzino'].items():
            for batch in data['scadenze']:
                if batch['sort'] < today: s = "☠️ SCADUTO"
                elif batch['sort'] <= limit: s = "⚠️ SCADE PRESTO"
                else: s = "🟢 OK"
                
                try: nome = df_master[df_master['Codice']==cod]['Descrizione'].iloc[0]
                except: nome = cod
                
                scad_list.append({"Stato": s, "Prodotto": nome, "Qta": batch['qty'], "Scadenza": batch['display']})
        
        if scad_list:
            st.dataframe(pd.DataFrame(scad_list).sort_values(by='Scadenza'), use_container_width=True, hide_index=True)
        else:
            st.info("Nessuna scadenza inserita.")

else:
    st.error("Errore Dati Master.")
