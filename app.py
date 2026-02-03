import streamlit as st
import pandas as pd
from datetime import datetime
import math
import io

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Gestione Magazzino Pro", layout="wide", initial_sidebar_state="collapsed")

# COSTANTI
MESI_COPERTURA = 2.0      # Intervallo tra i tuoi ordini
MESI_BUFFER = 0.5         # 2 settimane di attesa corriere
TARGET_MESI = MESI_COPERTURA + MESI_BUFFER # Totale 2.5 mesi da coprire
MIN_SCORTA_CAL = 3        # Minimo scatole per i Calibratori

# --- CARICAMENTO DATI ---
@st.cache_data
def load_master_data():
    try:
        # Legge il file Excel
        df = pd.read_excel('dati.xlsx', engine='openpyxl')
        
        # 1. Unione Codici
        if 'LN ABBOTT' in df.columns and 'LN ABBOTT AGGIORNATI' in df.columns:
            df['Codice_Finale'] = df['LN ABBOTT'].fillna(df['LN ABBOTT AGGIORNATI'])
        else:
            df['Codice_Finale'] = df.iloc[:, 4] 

        # 2. Mappatura Colonne
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
        
        # 3. Pulizia
        df = df[df['Descrizione'].notna()]
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False)
        
        # Conversione numeri
        for col in ['Test_Mensili_Reali', 'Test_per_Scatola', 'Fabbisogno_Kit_Mese_Stimato']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0

        df['Prodotto_Label'] = df['Descrizione'] + " [" + df['Codice'] + "]"
        
        return df
    except Exception as e:
        st.error(f"Errore nel file Excel: {e}")
        return pd.DataFrame()

# --- MEMORIA (Session State) ---
if 'magazzino' not in st.session_state:
    st.session_state['magazzino'] = {} 
if 'storico' not in st.session_state:
    st.session_state['storico'] = []

# --- INTERFACCIA ---
st.title("🏥 Magazzino - Gestione & Riordino")

df_master = load_master_data()

if not df_master.empty:
    
    tab_mov, tab_ordini, tab_scadenze = st.tabs(["⚡ Movimenti", "📦 Calcolo Ordine (2.5 Mesi)", "⚠️ Scadenze"])

    # === TAB 1: MOVIMENTI ===
    with tab_mov:
        col_sel, col_info = st.columns([3, 1])
        
        with col_sel:
            lista = df_master['Prodotto_Label'].tolist()
            prodotto_scelto = st.selectbox("Seleziona Prodotto:", lista)
            
        row_art = df_master[df_master['Prodotto_Label'] == prodotto_scelto].iloc[0]
        codice = row_art['Codice']
        categoria_art = str(row_art.get('Categoria', '')).upper()
        
        with col_info:
            st.info(f"Conf: {row_art.get('Confezione', '-')}\nTipo: {row_art.get('Categoria', 'ND')}")
            if "CAL" in categoria_art:
                st.warning(f"⚠️ CALIBRATORE\nScorta Minima: {MIN_SCORTA_CAL}")

        c1, c2 = st.columns([1, 2])
        with c1:
            qty = st.number_input("Quantità (Scatole)", min_value=1, value=1)
        with c2:
            tipo = st.radio("Azione", ["Prelievo ➖", "Carico ➕"], horizontal=True)

        # Scadenza
        scad_display, scad_sort = "-", None
        if "Carico" in tipo:
            cm, ca = st.columns(2)
            with cm: mese = st.selectbox("Mese Scad.", range(1, 13))
            with ca: anno = st.selectbox("Anno Scad.", range(datetime.now().year, datetime.now().year + 6))
            scad_display = f"{mese:02d}/{anno}"
            scad_sort = f"{anno}-{mese:02d}"

        if st.button("Registra", type="primary", use_container_width=True):
            if codice not in st.session_state['magazzino']:
                st.session_state['magazzino'][codice] = {'qty': 0, 'scadenze': []}
            
            ref = st.session_state['magazzino'][codice]
            
            if "Carico" in tipo:
                ref['qty'] += qty
                ref['scadenze'].append({'display': scad_display, 'sort': scad_sort, 'qty': qty})
                ref['scadenze'].sort(key=lambda x: x['sort'])
            else:
                if ref['qty'] < qty:
                    st.error("Non hai abbastanza scatole!")
                    st.stop()
                ref['qty'] -= qty
                # FIFO Logic
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
            
            st.session_state['storico'].insert(0, {
                "Data": datetime.now().strftime("%d/%m %H:%M"),
                "Prodotto": row_art['Descrizione'],
                "Azione": "➕" if "Carico" in tipo else "➖",
                "Qta": qty,
                "Giacenza": ref['qty']
            })
            st.success("Fatto!")

    # === TAB 2: CALCOLO ORDINE ===
    with tab_ordini:
        st.markdown(f"### 📊 Ordine per Copertura {TARGET_MESI} Mesi")
        st.caption(f"Nota: Per i prodotti CAL (Calibratori) il sistema impone un minimo di {MIN_SCORTA_CAL} scatole.")
        
        df_calc = df_master.copy()
        
        # Recupera giacenza
        df_calc['Giacenza'] = df_calc['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
        
        # 1. Calcolo Consumo Mensile
        def calcola_consumo_scatole(row):
            if row['Test_Mensili_Reali'] > 0 and row['Test_per_Scatola'] > 0:
                return row['Test_Mensili_Reali'] / row['Test_per_Scatola']
            if row['Fabbisogno_Kit_Mese_Stimato'] > 0:
                return row['Fabbisogno_Kit_Mese_Stimato']
            return 0 

        df_calc['Consumo_Mensile_Scatole'] = df_calc.apply(calcola_consumo_scatole, axis=1)
        
        # 2. Calcolo Obiettivo Scorta (CON REGOLA CALIBRATORI)
        def calcola_target(row):
            # Calcolo base matematico
            base_target = math.ceil(row['Consumo_Mensile_Scatole'] * TARGET_MESI)
            
            # Controllo se è un Calibratore
            categoria = str(row['Categoria']).upper()
            if "CAL" in categoria:
                # Se è Calibratore, il target è ALMENO 3, oppure quello calcolato se maggiore
                return max(base_target, MIN_SCORTA_CAL)
            
            return base_target

        df_calc['Scorta_Target'] = df_calc.apply(calcola_target, axis=1)
        
        # 3. Calcolo Da Ordinare
        df_calc['Da_Ordinare'] = df_calc.apply(lambda x: max(0, x['Scorta_Target'] - x['Giacenza']), axis=1)
        
        # 4. Copertura
        def calc_copertura(row):
            if row['Consumo_Mensile_Scatole'] <= 0: return 99.9
            return row['Giacenza'] / row['Consumo_Mensile_Scatole']
            
        df_calc['Mesi_Autonomia'] = df_calc.apply(calc_copertura, axis=1)
        
        # 5. Semaforo
        def get_semaforo(row):
            # Se è Calibratore e siamo sotto scorta minima
            categoria = str(row['Categoria']).upper()
            if "CAL" in categoria and row['Giacenza'] < MIN_SCORTA_CAL:
                return "🔴 SOTTO MINIMO (CAL)"

            if row['Consumo_Mensile_Scatole'] == 0 and "CAL" not in categoria: return "⚪ Dati mancanti"
            if row['Giacenza'] == 0: return "🔴 ESAURITO"
            if row['Mesi_Autonomia'] < MESI_BUFFER: return "🟠 URGENTE"
            if row['Da_Ordinare'] > 0: return "🟡 RIORDINARE"
            return "🟢 COPERTO"

        df_calc['Stato'] = df_calc.apply(get_semaforo, axis=1)
        
        # Visualizzazione
        df_view = df_calc.sort_values(by=['Da_Ordinare'], ascending=False)
        
        if st.checkbox("Nascondi Prodotti OK", value=True):
            df_view = df_view[df_view['Stato'] != "🟢 COPERTO"]

        st.dataframe(
            df_view[['Stato', 'Descrizione', 'Categoria', 'Giacenza', 'Scorta_Target', 'Da_Ordinare']],
            use_container_width=True,
            column_config={
                "Scorta_Target": st.column_config.NumberColumn("Target", help=f"Obiettivo (Minimo {MIN_SCORTA_CAL} per CAL)"),
                "Da_Ordinare": st.column_config.NumberColumn("🛒 DA ORDINARE")
            }
        )
        
        # Export
        if st.button("📥 Scarica Lista Ordine"):
            df_out = df_calc[df_calc['Da_Ordinare'] > 0][['Codice', 'Descrizione', 'Da_Ordinare', 'Confezione']]
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_out.to_excel(writer, index=False)
            st.download_button("Download Excel", data=buffer.getvalue(), file_name="ordine_bimestrale.xlsx")

    # === TAB 3: SCADENZE ===
    with tab_scadenze:
        st.markdown("### 📅 Controllo Scadenze")
        scad_list = []
        today_str = datetime.now().strftime("%Y-%m")
        limit_str = (datetime.now() + pd.DateOffset(months=3)).strftime("%Y-%m")
        
        for cod, data in st.session_state['magazzino'].items():
            for batch in data['scadenze']:
                status = "🟢"
                if batch['sort'] < today_str: status = "☠️ SCADUTO"
                elif batch['sort'] <= limit_str: status = "⚠️ SCADE A BREVE"
                
                scad_list.append({
                    "Stato": status,
                    "Prodotto": df_master[df_master['Codice']==cod]['Descrizione'].iloc[0],
                    "Quantità": batch['qty'],
                    "Scadenza": batch['display'],
                    "Sort": batch['sort']
                })
        
        if scad_list:
            df_scad = pd.DataFrame(scad_list).sort_values(by='Sort')
            st.dataframe(df_scad[['Stato', 'Prodotto', 'Quantità', 'Scadenza']], use_container_width=True)
        else:
            st.info("Nessuna scadenza critica.")

else:
    st.error("Errore Caricamento: Controlla che 'dati.xlsx' sia su GitHub.")
