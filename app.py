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

# --- STILE CSS ---
st.markdown("""
    <style>
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
    
    /* CUSTOM LOADER */
    .stSpinner { display: none; }
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
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    @keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
    </style>
    """, unsafe_allow_html=True)

# --- PARAMETRI ---
MESI_COPERTURA = 1.0      
MESI_BUFFER = 0.25        
TARGET_MESI = MESI_COPERTURA + MESI_BUFFER 
MIN_SCORTA_CAL = 3        

# --- CONNESSIONE ---
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("⚠️ Errore Segreti: Configura .streamlit/secrets.toml")
    st.stop()

# --- DATI MASTER ---
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
        
        if 'Confezione' not in df.columns:
            df['Confezione'] = ""
            
        df = df[df['Descrizione'].notna() & df['Codice'].notna()] 
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False)
        
        # --- SOSTITUZIONE CODICI OBSOLETI ---
        df.loc[df['Codice'].str.contains("8P0602|8P06-02", case=False, na=False), 'Codice'] = "06T7901"
        
        df['Categoria'] = df['Categoria'].astype(str).fillna('')
        df['Assay_Name'] = df['Assay_Name'].astype(str).fillna('')
        
        def clean_custom_values(val):
            if pd.isna(val): return val
            s = str(val).strip()
            if "25-30" in s: return 30        
            if "28" in s and "?" in s: return 4   
            if "12/15" in s: return 15        
            return val

        df['Fabbisogno_Kit_Mese_Stimato'] = df['Fabbisogno_Kit_Mese_Stimato'].apply(clean_custom_values)
        
        # --- FORZATURE PREVENTIVE UNIFICATE ---
        df.loc[df['Codice'].str.contains("8P0852|8P08-52", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 2
        df.loc[df['Codice'].str.contains("9P4922|9P49-22", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 4
        df.loc[df['Codice'].str.contains("7P5320|7P53-20", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 2
        df.loc[df['Codice'].str.contains("06Q1461|06Q14-61", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 9
        df.loc[df['Codice'].str.contains("1R3801|1R38-01", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 6
        df.loc[df['Codice'].str.contains("6P1401|6P14-01", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 45
        df.loc[df['Codice'].str.contains("8P9870|8P98-70", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 1
        df.loc[df['Codice'].str.contains("0L10501|0L10-50", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 2
        df.loc[df['Codice'].str.contains("0L10601|0L10-60", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 2
        df.loc[df['Codice'].str.contains("1R1922|1R19-22", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 1
        
        # FIX CALIBRATORI: Forziamo la categoria a "CAL" e nomi
        df.loc[df['Codice'].str.contains("08P6001|08P60-01", case=False, na=False), 'Descrizione'] = 'MC MCC CALS'
        df.loc[df['Codice'].str.contains("08P6001|08P60-01", case=False, na=False), 'Categoria'] = 'CAL'
        df.loc[df['Codice'].str.contains("06T7901", case=False, na=False), 'Categoria'] = 'CAL'
        df.loc[df['Codice'].str.contains("0L10701|0L10-70", case=False, na=False), 'Categoria'] = 'CAL'
        df.loc[df['Codice'].str.contains("1R1901|1R19-01", case=False, na=False), 'Categoria'] = 'CAL'
        
        df['Kit_Mese_Numeric'] = pd.to_numeric(df['Fabbisogno_Kit_Mese_Stimato'], errors='coerce')
        
        for col in ['Test_Mensili_Reali', 'Test_per_Scatola']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0
                
        df.loc[df['Codice'].str.contains("09P2820|09P28-20", case=False, na=False), 'Test_Mensili_Reali'] = 1000
                
        def calcola_kit_mancanti(row):
            if pd.isna(row['Kit_Mese_Numeric']) or row['Kit_Mese_Numeric'] == 0:
                if row['Test_Mensili_Reali'] > 0 and row['Test_per_Scatola'] > 0:
                    return row['Test_Mensili_Reali'] / row['Test_per_Scatola']
            return row['Kit_Mese_Numeric']
            
        df['Kit_Mese_Numeric'] = df.apply(calcola_kit_mancanti, axis=1)
        df['Kit_Mese_Numeric'] = df['Kit_Mese_Numeric'].fillna(0)
        
        # --- REGOLE DI INCLUSIONE NEL MAGAZZINO ---
        has_valid_consumption = df['Kit_Mese_Numeric'] > 0
        is_cal = df['Categoria'].str.upper().str.contains("CAL", na=False)
        
        is_special = df['Descrizione'].str.contains("VANCOMICINA|BARBITURICI|TRAB|HBsAg Quant|Tireoglobulina|ICT SAMPLE DILUENT|Omocisteina|SECONDARY TUBES|Sample Cups|Reaction Vessels|Maintenance Solutions|Mioglobina|Procalcitonina|MC MCC CALS|Rame|Zinco|Cu-Zn|NSE", case=False, na=False) | \
                     df['Assay_Name'].str.contains("VANCOMICINA|BARBITURICI|TRAB|HBsAg Quant|Tireoglobulina|ICT SAMPLE DILUENT|Omocisteina|SECONDARY TUBES|Sample Cups|Reaction Vessels|Maintenance Solutions|Mioglobina|Procalcitonina|MC MCC CALS|Rame|Zinco|Cu-Zn|NSE", case=False, na=False) | \
                     df['Codice'].str.contains("8P0852|9P4922|7P5320|09P2820|06Q1461|1R3801|6P1401|8P9870|4V3730|1R1822|08P6001|06T7901|0L10501|0L10601|0L10701|1R1901|1R1922", case=False, na=False)
        
        df = df[has_valid_consumption | is_cal | is_special]

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
                
                if 'Ultima_Modifica' in df_db.columns:
                    um = str(row['Ultima_Modifica'])
                    if um == 'nan' or not um.strip(): um = '2000-01-01 00:00:00'
                else:
                    um = '2000-01-01 00:00:00'
                    
                magazzino[cod] = {'qty': qty, 'scadenze': scadenze, 'ultima_modifica': um}
        return magazzino
    except: return {}

def update_inventory(magazzino_dict):
    data_list = []
    for cod, info in magazzino_dict.items():
        if info['qty'] > 0: 
            um = info.get('ultima_modifica', '2000-01-01 00:00:00')
            data_list.append({
                "Codice": cod,
                "Quantita": info['qty'],
                "Scadenze_JSON": json.dumps(info['scadenze']),
                "Ultima_Modifica": um
            })
    
    if not data_list:
        df_new = pd.DataFrame(columns=["Codice", "Quantita", "Scadenze_JSON", "Ultima_Modifica"])
    else:
        df_new = pd.DataFrame(data_list)
        
    conn.update(worksheet="Foglio1", data=df_new)

def manage_log_cloud(azione, prodotto_nome, qta):
    try:
        now = datetime.now()
        new_row = {
            "Timestamp": now,
            "Data_Leggibile": now.strftime("%d/%m %H:%M"),
            "Azione": f"{azione} ({qta})",
            "Prodotto": prodotto_nome
        }
        
        if 'cloud_log' in st.session_state and not st.session_state['cloud_log'].empty:
            df_log = st.session_state['cloud_log'].copy()
        else:
            df_log = pd.DataFrame(columns=["Timestamp", "Data_Leggibile", "Azione", "Prodotto"])
        
        df_log = pd.concat([pd.DataFrame([new_row]), df_log], ignore_index=True)
        
        days_ago_30 = now - timedelta(days=30)
        df_log['Timestamp'] = pd.to_datetime(df_log['Timestamp'])
        df_log_clean = df_log[df_log['Timestamp'] > days_ago_30]
        
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

# --- HEADER ---
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
    st.header("📋 LOG (Ultimi 30gg)")
    
    if 'cloud_log' not in st.session_state:
        st.session_state['cloud_log'] = fetch_only_log()
    
    if st.button("🔄 Aggiorna Log"):
        st.session_state['cloud_log'] = fetch_only_log()
        st.rerun()

    if not st.session_state['cloud_log'].empty:
        show_log = st.session_state['cloud_log'][['Data_Leggibile', 'Azione', 'Prodotto']].head(100)
        log_height = max(150, len(show_log) * 36 + 43)
        st.dataframe(show_log, hide_index=True, use_container_width=True, height=log_height)
    else:
        st.caption("Nessun evento recente.")

df_master = load_master_data()

if 'magazzino' not in st.session_state:
    with st.spinner("⏳ Sincronizzazione Cloud..."):
        st.session_state['magazzino'] = fetch_inventory()

if not df_master.empty:
    
    # --- FUNZIONE POP-UP DI CONFERMA ---
    @st.dialog("⚠️ Conferma Azzeramento")
    def open_reset_dialog(cod, nome):
        st.warning(f"Sei sicuro di voler azzerare le quantità di **{nome}**?")
        st.caption("Questa azione eliminerà anche lo storico e tutte le scadenze inserite per questo prodotto.")
        
        c_no, c_yes = st.columns(2)
        
        if c_no.button("❌ Annulla", use_container_width=True):
            st.rerun()
            
        if c_yes.button("✅ Sì, Azzera", type="primary", use_container_width=True):
            loader_placeholder = st.empty()
            loader_placeholder.markdown("""<div id="custom-loader"><div class="spinner"></div><div class="loading-text">Azzeramento in corso...</div></div>""", unsafe_allow_html=True)
            
            ref = st.session_state['magazzino'][cod]
            old_qty = ref['qty']
            
            # Reset radicale a zero
            ref['qty'] = 0
            ref['scadenze'] = []
            ref['ultima_modifica'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            update_inventory(st.session_state['magazzino'])
            st.session_state['cloud_log'] = manage_log_cloud("Reset Scorte", nome, f"{old_qty} -> 0")
            
            loader_placeholder.empty()
            st.toast("✅ Scorte azzerate con successo!", icon="🗑️")
            time.sleep(0.5) 
            st.rerun()


    tab_mov, tab_ordini, tab_controlli, tab_scadenze = st.tabs(["⚡ OPERAZIONI", "🛒 ORDINI & ANALISI", "⏳ DA VERIFICARE", "🗓️ SCADENZE"])

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
            
            scelta = st.selectbox("Cerca Prodotto (Nome, Codice, Assay):", opzioni, index=None, placeholder="Digita per cercare...")
            
        if scelta:
            df_master['Menu_Label'] = df_master.apply(get_label, axis=1)
            row_art = df_master[df_master['Menu_Label'] == scelta].iloc[0]
            codice = str(row_art['Codice'])
            
            with col_dati:
                giacenza_attuale = st.session_state['magazzino'].get(codice, {}).get('qty', 0)
                st.metric("Giacenza Attuale", f"{int(giacenza_attuale)}", delta="scatole")
                if "CAL" in str(row_art['Categoria']).upper():
                    st.warning("⚠️ Calibratore")

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

                st.markdown("<br>", unsafe_allow_html=True)
                col_btn1, col_btn2 = st.columns([3, 1])

                if col_btn1.button("🚀 ESEGUI OPERAZIONE", type="primary", use_container_width=True):
                    loader_placeholder = st.empty()
                    loader_placeholder.markdown("""<div id="custom-loader"><div class="spinner"></div><div class="loading-text">Salvataggio in Cloud...</div></div>""", unsafe_allow_html=True)
                    
                    if codice not in st.session_state['magazzino']:
                        st.session_state['magazzino'][codice] = {'qty': 0, 'scadenze': [], 'ultima_modifica': '2000-01-01 00:00:00'}
                    
                    ref = st.session_state['magazzino'][codice]
                    tipo_azione_log = ""
                    err = False

                    if "CARICO" in azione:
                        ref['qty'] += qty_input
                        ref['scadenze'].append({'display': scad_display, 'sort': scad_sort, 'qty': qty_input})
                        ref['scadenze'].sort(key=lambda x: x['sort'])
                        tipo_azione_log = "Carico"
                    elif "PRELIEVO" in azione:
                        if ref['qty'] < qty_input: err = True
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
                                    else: rem -= batch['qty']
                                else: new_scad.append(batch)
                            ref['scadenze'] = new_scad
                            tipo_azione_log = "Prelievo"
                    elif "RETTIFICA" in azione:
                        diff = qty_input - ref['qty']
                        if diff == 0: 
                            err = False
                            tipo_azione_log = "Conferma Giacenza"
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
                                        else: da_togliere -= batch['qty']
                                    else: new_scad.append(batch)
                                ref['scadenze'] = new_scad
                            tipo_azione_log = "Rettifica"

                    if err:
                        loader_placeholder.empty()
                        st.error("Quantità insufficiente!")
                    else:
                        ref['ultima_modifica'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        update_inventory(st.session_state['magazzino'])
                        qta_str = str(qty_input)
                        if "RETTIFICA" in azione: qta_str = f"OK: {qty_input}" if tipo_azione_log == "Conferma Giacenza" else f"-> {qty_input}"
                        st.session_state['cloud_log'] = manage_log_cloud(tipo_azione_log, row_art['Descrizione'], qta_str)
                        loader_placeholder.empty()
                        st.toast(f"✅ Salvato!", icon="☁️")
                        time.sleep(0.5) 
                        st.rerun()

                if col_btn2.button("🗑️ AZZERA (0)", use_container_width=True):
                    if codice not in st.session_state['magazzino']:
                        st.session_state['magazzino'][codice] = {'qty': 0, 'scadenze': [], 'ultima_modifica': '2000-01-01 00:00:00'}
                    open_reset_dialog(codice, row_art['Descrizione'])

    # === TAB 2: ORDINI ===
    with tab_ordini:
        st.markdown("### 🚦 Analisi Fabbisogno (1.25 Mesi)")
        
        c_search, c_filtro = st.columns([2,1])
        term = c_search.text_input("🔍 Cerca (Nome, Codice, Assay)...", placeholder="Scrivi qui...")
        filtro = c_filtro.multiselect("Filtra Stato:", ["🔴 SOTTO MINIMO", "🔴 ESAURITO", "🟡 DA ORDINARE", "🟢 OK"], default=["🔴 SOTTO MINIMO", "🔴 ESAURITO", "🟡 DA ORDINARE"])
        
        df_c = df_master.copy()
        df_c['Giacenza'] = df_c['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
        
        def calcola_stato(row):
            cod_pulito = str(row['Codice']).upper().replace("-", "").strip()
            consumo = row.get('Kit_Mese_Numeric', 0)
            target = math.ceil(consumo * TARGET_MESI)
            
            if "4V3730" in cod_pulito: target += 1
            elif "1R1822" in cod_pulito: target += 2
            
            target = max(target, 2)
            
            if "CAL" in str(row['Categoria']).upper(): target = max(target, MIN_SCORTA_CAL)
            
            da_ord = max(0, target - row['Giacenza'])
            days_left = int(row['Giacenza'] / (consumo/30)) if consumo > 0 and row['Giacenza'] > 0 else None
            
            stato = "🟢 OK"
            if "CAL" in str(row['Categoria']).upper() and row['Giacenza'] < MIN_SCORTA_CAL: stato = "🔴 SOTTO MINIMO"
            elif row['Giacenza'] == 0: stato = "🔴 ESAURITO"
            elif da_ord > 0: stato = "🟡 DA ORDINARE"
            
            return pd.Series([stato, target, da_ord, days_left])

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
        
        ordini_height = max(150, len(df_view) * 36 + 43)
        
        st.dataframe(
            df_view[['Stato', 'Categoria', 'Assay_Name', 'Descrizione', 'Codice', 'Giacenza', 'Target', 'Days_Left', 'Da_Ordinare']],
            use_container_width=True,
            hide_index=True,
            height=ordini_height,
            column_config={
                "Stato": st.column_config.TextColumn("Stato", width="small"),
                "Categoria": st.column_config.TextColumn("Tipo", width="small"),
                "Assay_Name": st.column_config.TextColumn("Assay", width="medium"),
                "Descrizione": st.column_config.TextColumn("Prodotto", width="large"),
                "Codice": st.column_config.TextColumn("LN Abbott", width="medium"),
                "Target": st.column_config.NumberColumn("Target"),
                "Days_Left": st.column_config.NumberColumn("Copertura", format="%d gg", help="Giorni di autonomia stimati"),
                "Da_Ordinare": st.column_config.NumberColumn("🛒 ORDINA")
            }
        )

        st.divider()
        st.write("### 📤 Esporta per Fornitore")
        
        df_export = df_c[df_c['Da_Ordinare'] > 0].copy()
        
        if not df_export.empty:
            cols_to_export = ['Codice', 'Categoria', 'Descrizione', 'Da_Ordinare']
            if 'Confezione' in df_export.columns:
                cols_to_export.append('Confezione')
                
            df_export = df_export[cols_to_export]
            
            rename_map = {
                'Codice': 'Codice Prodotto', 
                'Categoria': 'Tipo',
                'Da_Ordinare': 'Qta Ordine', 
                'Confezione': 'Conf.to'
            }
            df_export = df_export.rename(columns=rename_map)
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_export.to_excel(writer, index=False)
                
            st.download_button(
                "📥 Scarica Ordine (Excel)", 
                data=buffer.getvalue(), 
                file_name=f"ordine_abbott_{datetime.now().strftime('%Y-%m-%d')}.xlsx", 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                type="primary"
            )
        else:
            st.success("Tutti i prodotti sono sopra il livello di guardia. Nessun ordine necessario!")

    # === TAB 3: DA VERIFICARE ===
    with tab_controlli:
        st.markdown("### ⏳ Allarme Giacenze Latenti (> 30 Giorni)")
        st.write("Prodotti non movimentati o confermati da oltre 30 giorni, divisi per categoria.")
        
        ver_rgt = []
        ver_cal = []
        ver_cons = []
        ver_altro = []
        
        now_dt = datetime.now()
        for _, row in df_master.iterrows():
            cod = str(row['Codice'])
            cat_upper = str(row['Categoria']).upper()
            info = st.session_state['magazzino'].get(cod, {})
            um_str = info.get('ultima_modifica', '2000-01-01 00:00:00')
            days_passed = 999 if um_str.startswith('2000') else (now_dt - datetime.strptime(um_str, "%Y-%m-%d %H:%M:%S")).days
            
            if days_passed >= 30:
                item = {
                    "Stato": "🚨 URGENTE" if info.get('qty',0) > 0 else "⚠️ VERIFICA", 
                    "Codice": cod, 
                    "Prodotto": row['Descrizione'], 
                    "Giacenza": info.get('qty', 0), 
                    "Ultima Modifica": um_str[:10], 
                    "Giorni": days_passed
                }
                
                if "RGT" in cat_upper:
                    ver_rgt.append(item)
                elif "CAL" in cat_upper:
                    ver_cal.append(item)
                elif "CONS" in cat_upper:
                    ver_cons.append(item)
                else:
                    ver_altro.append(item)
        
        has_items = False
        
        def render_ver_table(data_list, title, icon):
            if data_list:
                with st.container():
                    st.subheader(f"{icon} {title}")
                    df_ver = pd.DataFrame(data_list).sort_values(by='Giorni', ascending=False)
                    ver_height = max(150, len(df_ver) * 36 + 43)
                    st.dataframe(df_ver, use_container_width=True, hide_index=True, height=ver_height)
                st.markdown("<br>", unsafe_allow_html=True)
                return True
            return False

        has_items |= render_ver_table(ver_rgt, "REAGENTI (RGT)", "🧪")
        has_items |= render_ver_table(ver_cal, "CALIBRATORI (CAL)", "⚖️")
        has_items |= render_ver_table(ver_cons, "CONSUMABILI (CONS)", "📦")
        has_items |= render_ver_table(ver_altro, "ALTRO (Controlli, Varie)", "🏷️")
        
        if not has_items: 
            st.success("🎉 Tutto aggiornato! Nessun prodotto è fermo da oltre 30 giorni.")

    # === TAB 4: SCADENZE ===
    with tab_scadenze:
        st.markdown("### 🗓️ Monitoraggio Scadenze Lotti")
        
        cal_list = []
        rgt_list = []
        today = datetime.now().strftime("%Y-%m")
        limit = (datetime.now() + pd.DateOffset(months=2)).strftime("%Y-%m")
        
        for cod, data in st.session_state['magazzino'].items():
            try: 
                master_row = df_master[df_master['Codice']==cod].iloc[0]
                nome = master_row['Descrizione']
                categoria = str(master_row['Categoria']).upper()
            except: 
                nome = cod
                categoria = ""
                
            for batch in data['scadenze']:
                s = "☠️ SCADUTO" if batch['sort'] < today else ("⚠️ PRESTO" if batch['sort'] <= limit else "🟢 OK")
                item = {"Stato": s, "Codice Prodotto": cod, "Prodotto": nome, "Qta": batch['qty'], "Scadenza": batch['display']}
                
                if "CAL" in categoria:
                    cal_list.append(item)
                else:
                    rgt_list.append(item)
        
        # --- TABELLA CALIBRATORI ---
        if cal_list:
            with st.container():
                st.subheader("🧪 CALIBRATORI")
                df_cal = pd.DataFrame(cal_list).sort_values(by='Scadenza')
                cal_height = max(150, len(df_cal) * 36 + 43)
                st.dataframe(df_cal, use_container_width=True, hide_index=True, height=cal_height)
                
                df_cal_exp = df_cal[df_cal['Stato'].isin(['☠️ SCADUTO', '⚠️ PRESTO'])]
                
                # --- NUOVA LOGICA: Filtro Esportazione Calibratori ---
                if not df_cal_exp.empty:
                    # Calcoliamo quante scatole buone (🟢 OK) ci sono in magazzino per ogni calibratore
                    valid_qty_cal = {}
                    for _, row in df_cal.iterrows():
                        c = row['Codice Prodotto']
                        if c not in valid_qty_cal: valid_qty_cal[c] = 0
                        if row['Stato'] == '🟢 OK':
                            valid_qty_cal[c] += row['Qta']
                    
                    # Escludiamo dall'Excel i calibratori che hanno già 3 o più scatole sane
                    df_cal_exp = df_cal_exp[df_cal_exp['Codice Prodotto'].apply(lambda c: valid_qty_cal.get(c, 0) < MIN_SCORTA_CAL)]
                
                if not df_cal_exp.empty:
                    buffer_cal = io.BytesIO()
                    with pd.ExcelWriter(buffer_cal, engine='openpyxl') as writer:
                        df_cal_exp.to_excel(writer, index=False)
                    st.download_button(
                        "📥 Esporta Calibratori in Scadenza (Excel)", 
                        data=buffer_cal.getvalue(), 
                        file_name=f"calibratori_in_scadenza_{datetime.now().strftime('%Y%m%d')}.xlsx", 
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="btn_exp_cal"
                    )
                else:
                    st.success("Tutti i calibratori sono al sicuro! (Le scorte valide sono sufficienti o le scadenze sono lontane) ✅")
        
        if cal_list and rgt_list: 
            st.markdown("<br><br>", unsafe_allow_html=True)

        # --- TABELLA REAGENTI E CONSUMABILI ---
        if rgt_list:
            with st.container():
                st.subheader("📦 REAGENTI E CONSUMABILI")
                df_rgt = pd.DataFrame(rgt_list).sort_values(by='Scadenza')
                rgt_height = max(150, len(df_rgt) * 36 + 43)
                st.dataframe(df_rgt, use_container_width=True, hide_index=True, height=rgt_height)
                
                df_rgt_exp = df_rgt[df_rgt['Stato'].isin(['☠️ SCADUTO', '⚠️ PRESTO'])]
                if not df_rgt_exp.empty:
                    buffer_rgt = io.BytesIO()
                    with pd.ExcelWriter(buffer_rgt, engine='openpyxl') as writer:
                        df_rgt_exp.to_excel(writer, index=False)
                    st.download_button(
                        "📥 Esporta Reagenti in Scadenza (Excel)", 
                        data=buffer_rgt.getvalue(), 
                        file_name=f"reagenti_in_scadenza_{datetime.now().strftime('%Y%m%d')}.xlsx", 
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="btn_exp_rgt"
                    )
                else:
                    st.success("Tutti i reagenti hanno scadenze lontane! ✅")
            
        if not cal_list and not rgt_list:
            st.info("Nessuna scadenza inserita in magazzino.")

else:
    st.error("Errore Dati Master.")
