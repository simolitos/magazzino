import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime
import math
import io
import json

# --- CONFIGURAZIONE ---
st.set_page_config(page_title="Magazzino Pro", layout="wide", initial_sidebar_state="collapsed")

MESI_COPERTURA = 1.0      
MESI_BUFFER = 0.5         
TARGET_MESI = MESI_COPERTURA + MESI_BUFFER 
MIN_SCORTA_CAL = 3        

# --- CONNESSIONE GOOGLE SHEETS ---
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("Errore di connessione ai Segreti. Controlla di aver configurato .streamlit/secrets.toml")
    st.stop()

# --- CARICAMENTO DATI ---
@st.cache_data
def load_master_data():
    try:
        # Legge il file Excel (Catalogo Prodotti) da GitHub
        df = pd.read_excel('dati.xlsx', engine='openpyxl')
        
        # Gestione colonne
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
            'Conf.to': 'Confezione'
        }
        
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        
        # Pulizia
        df = df[df['Descrizione'].notna()] 
        df = df[df['Codice'].notna()]      
        
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False)
        
        for col in ['Test_Mensili_Reali', 'Test_per_Scatola', 'Fabbisogno_Kit_Mese_Stimato']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0

        df['Prodotto_Label'] = df['Descrizione'] + " [" + df['Codice'] + "]"
        return df
    except Exception as e:
        st.error(f"Errore file Excel Master: {e}")
        return pd.DataFrame()

# --- FUNZIONI DATABASE ONLINE ---
def fetch_inventory():
    """Scarica la giacenza aggiornata da Google Sheets"""
    try:
        df_db = conn.read(worksheet="Foglio1", ttl=0)
        magazzino = {}
        if not df_db.empty and 'Codice' in df_db.columns:
            df_db['Codice'] = df_db['Codice'].astype(str)
            for _, row in df_db.iterrows():
                cod = str(row['Codice'])
                qty = row['Quantita']
                try:
                    scadenze = json.loads(row['Scadenze_JSON'])
                except:
                    scadenze = []
                magazzino[cod] = {'qty': qty, 'scadenze': scadenze}
        return magazzino
    except Exception as e:
        return {}

def update_inventory(magazzino_dict):
    """Salva la giacenza su Google Sheets"""
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

# --- APP ---
st.title("☁️ Magazzino Cloud")

df_master = load_master_data()

# Caricamento iniziale
if 'magazzino' not in st.session_state:
    with st.spinner("Connessione al database..."):
        st.session_state['magazzino'] = fetch_inventory()

if not df_master.empty:

    tab_mov, tab_ordini, tab_scadenze = st.tabs(["⚡ Movimenti", "🚦 Situazione Ordini", "⚠️ Scadenze"])

    # === TAB MOVIMENTI ===
    with tab_mov:
        if st.button("🔄 Aggiorna Dati (Refresh)"):
            st.session_state['magazzino'] = fetch_inventory()
            st.rerun()

        col_sel, col_info = st.columns([3, 1])
        with col_sel:
            def get_label(row):
                cod = str(row['Codice'])
                giacenza = st.session_state['magazzino'].get(cod, {}).get('qty', 0)
                return f"{row['Descrizione']} [Giac: {giacenza}]"
            
            opzioni = df_master.apply(get_label, axis=1).tolist()
            scelta_label = st.selectbox("Seleziona Prodotto:", opzioni)
            desc_base = scelta_label.split(" [Giac:")[0]
            row_art = df_master[df_master['Descrizione'] == desc_base].iloc[0]

        codice = str(row_art['Codice'])
        categoria = str(row_art.get('Categoria', '')).upper()
        
        with col_info:
            st.info(f"Conf: {row_art.get('Confezione', '-')}\nTipo: {categoria}")
            if "CAL" in categoria: st.warning(f"CALIBRATORE (Min {MIN_SCORTA_CAL})")

        c1, c2 = st.columns([1, 2])
        qty = c1.number_input("Quantità", min_value=1, value=1)
        tipo = c2.radio("Azione", ["Prelievo ➖", "Carico ➕"], horizontal=True)

        scad_display, scad_sort = "-", None
        if "Carico" in tipo:
            cm, ca = st.columns(2)
            with cm: mese = st.selectbox("Mese", range(1, 13))
            with ca: anno = st.selectbox("Anno", range(datetime.now().year, datetime.now().year + 6))
            scad_display = f"{mese:02d}/{anno}"
            scad_sort = f"{anno}-{mese:02d}"

        if st.button("💾 REGISTRA E SALVA ONLINE", type="primary"):
            if codice not in st.session_state['magazzino']:
                st.session_state['magazzino'][codice] = {'qty': 0, 'scadenze': []}
            
            ref = st.session_state['magazzino'][codice]
            
            if "Carico" in tipo:
                ref['qty'] += qty
                ref['scadenze'].append({'display': scad_display, 'sort': scad_sort, 'qty': qty})
                ref['scadenze'].sort(key=lambda x: x['sort'])
            else:
                if ref['qty'] < qty:
                    st.error("Giacenza insufficiente!")
                    st.stop()
                ref['qty'] -= qty
                rem = qty
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
            
            with st.spinner("Salvataggio su Google Sheets in corso..."):
                update_inventory(st.session_state['magazzino'])
            
            st.success("✅ Salvato online!")
            st.rerun()

    # === TAB ORDINI (AGGIORNATA) ===
    with tab_ordini:
        st.markdown(f"### 🚦 Pannello Controllo Scorte")
        
        # Barra di Ricerca
        search_term = st.text_input("🔍 Cerca prodotto (Nome, Codice o Categoria)...", "")
        
        df_calc = df_master.copy()
        df_calc['Giacenza'] = df_calc['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
        
        def calcola_target(row):
            consumo = 0
            if row['Test_Mensili_Reali'] > 0 and row['Test_per_Scatola'] > 0:
                consumo = row['Test_Mensili_Reali'] / row['Test_per_Scatola']
            elif row['Fabbisogno_Kit_Mese_Stimato'] > 0:
                consumo = row['Fabbisogno_Kit_Mese_Stimato']
            
            target = math.ceil(consumo * TARGET_MESI)
            if "CAL" in str(row['Categoria']).upper(): return max(target, MIN_SCORTA_CAL)
            return target

        df_calc['Scorta_Target'] = df_calc.apply(calcola_target, axis=1)
        df_calc['Da_Ordinare'] = df_calc.apply(lambda x: max(0, x['Scorta_Target'] - x['Giacenza']), axis=1)
        
        # Funzione Semaforo
        def get_semaforo(row):
            categoria = str(row['Categoria']).upper()
            if "CAL" in categoria and row['Giacenza'] < MIN_SCORTA_CAL: return "🔴 SOTTO MINIMO"
            if row['Giacenza'] == 0: return "🔴 ESAURITO"
            if row['Da_Ordinare'] > 0: return "🟡 DA ORDINARE"
            return "🟢 OK"

        df_calc['Stato'] = df_calc.apply(get_semaforo, axis=1)
        
        # FILTRO DI VISUALIZZAZIONE
        col_filtro1, col_filtro2 = st.columns([2, 1])
        with col_filtro1:
            filtro_stati = st.multiselect(
                "Filtra per stato (Lascia vuoto per vedere TUTTO):", 
                ["🔴 SOTTO MINIMO", "🔴 ESAURITO", "🟡 DA ORDINARE", "🟢 OK"],
                default=["🔴 SOTTO MINIMO", "🔴 ESAURITO", "🟡 DA ORDINARE"] # Default intelligente
            )
        
        # 1. Applica Filtro Stati (Se vuoto = Mostra Tutto)
        if not filtro_stati:
            df_view = df_calc # Mostra tutto
        else:
            df_view = df_calc[df_calc['Stato'].isin(filtro_stati)] # Mostra solo selezionati

        # 2. Applica Ricerca Testuale
        if search_term:
            # Cerca su Descrizione o Categoria o Codice
            mask = (
                df_view['Descrizione'].str.contains(search_term, case=False, na=False) | 
                df_view['Categoria'].str.contains(search_term, case=False, na=False) |
                df_view['Codice'].str.contains(search_term, case=False, na=False)
            )
            df_view = df_view[mask]
            
        # Ordinamento
        df_view = df_view.sort_values(by=['Da_Ordinare'], ascending=False)
        
        # MOSTRA TABELLA (Con Categoria)
        st.dataframe(
            df_view[['Stato', 'Categoria', 'Descrizione', 'Giacenza', 'Scorta_Target', 'Da_Ordinare']], 
            use_container_width=True,
            column_config={
                "Stato": st.column_config.TextColumn("Stato", width="small"),
                "Categoria": st.column_config.TextColumn("Cat.", width="small"),
                "Da_Ordinare": st.column_config.NumberColumn("🛒 Da Ordinare", format="%d")
            }
        )
        
        if st.button("📥 Scarica Lista Ordine"):
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_view.to_excel(writer, index=False)
            st.download_button("Download Excel", data=buffer.getvalue(), file_name="ordine_cloud.xlsx")

    # === TAB SCADENZE ===
    with tab_scadenze:
        scad_list = []
        today = datetime.now().strftime("%Y-%m")
        limit = (datetime.now() + pd.DateOffset(months=3)).strftime("%Y-%m")
        
        for cod, data in st.session_state['magazzino'].items():
            for batch in data['scadenze']:
                st_scad = "🟢"
                if batch['sort'] < today: st_scad = "☠️ SCADUTO"
                elif batch['sort'] <= limit: st_scad = "⚠️ SCADE A BREVE"
                
                try: nome = df_master[df_master['Codice']==cod]['Descrizione'].iloc[0]
                except: nome = cod
                
                scad_list.append({"Stato": st_scad, "Prodotto": nome, "Qty": batch['qty'], "Scadenza": batch['display']})
        
        if scad_list: st.dataframe(pd.DataFrame(scad_list), use_container_width=True)
        else: st.info("Tutto ok.")

else:
    st.error("Errore caricamento dati master.")
