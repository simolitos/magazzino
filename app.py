import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import math
import io

# --- CONFIGURAZIONE ---
st.set_page_config(page_title="Gestione Magazzino (2 Mesi)", layout="wide", initial_sidebar_state="collapsed")

# COSTANTI DI GESTIONE
INTERVALLO_CONTROLLO_MESI = 2    # Ogni quanto controlli il magazzino
LEAD_TIME_MESI = 0.5             # 2 settimane di sicurezza per la consegna
TARGET_MESI = INTERVALLO_CONTROLLO_MESI + LEAD_TIME_MESI # Obiettivo: Coprire 2.5 mesi

# --- CARICAMENTO DATI ---
@st.cache_data
def load_master_data():
    try:
        df = pd.read_excel('dati.xlsx', engine='openpyxl')
        
        # Gestione Codici
        if 'LN ABBOTT' in df.columns and 'LN ABBOTT AGGIORNATI' in df.columns:
            df['Codice_Finale'] = df['LN ABBOTT'].fillna(df['LN ABBOTT AGGIORNATI'])
        else:
            df['Codice_Finale'] = df.iloc[:, 4] 

        # Mappatura
        col_map = {
            'Codice_Finale': 'Codice',
            'Descrizione commerciale': 'Descrizione',
            'Rgt/Cal/QC/Cons': 'Categoria',
            '# Kit/Mese': 'Fabbisogno_Kit_Mese', 
            'Test TOT MEDI/MESE Aggiustati': 'Consumo_Test_Mese',
            'KIT': 'Test_per_Scatola',
            'Conf.to': 'Confezione',
            'LOB': 'Reparto'
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        
        # Pulizia
        df = df[df['Descrizione'].notna()]
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False)
        
        # Conversione Numerica
        cols_to_numeric = ['Fabbisogno_Kit_Mese', 'Consumo_Test_Mese', 'Test_per_Scatola']
        for col in cols_to_numeric:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0

        df['Prodotto_Label'] = df['Descrizione'] + " [" + df['Codice'] + "]"
        return df
    except Exception as e:
        st.error(f"Errore file Excel: {e}")
        return pd.DataFrame()

# --- SESSION STATE ---
if 'magazzino' not in st.session_state:
    st.session_state['magazzino'] = {} 
if 'storico' not in st.session_state:
    st.session_state['storico'] = []

# --- APP ---
st.title("🏥 Magazzino - Gestione Bimestrale")

df_master = load_master_data()

if not df_master.empty:
    
    tab_mov, tab_ordini, tab_scadenze = st.tabs(["⚡ Movimenti", "📦 Calcolo Ordine (2 Mesi)", "⚠️ Scadenze"])

    # === TAB 1: MOVIMENTI ===
    with tab_mov:
        col_sel, col_info = st.columns([3, 1])
        with col_sel:
            lista_prodotti = df_master['Prodotto_Label'].tolist()
            prodotto_scelto = st.selectbox("Seleziona Prodotto:", lista_prodotti)
        
        row_art = df_master[df_master['Prodotto_Label'] == prodotto_scelto].iloc[0]
        codice_art = row_art['Codice']

        with col_info:
            st.info(f"Fabbisogno Mese: {row_art.get('Fabbisogno_Kit_Mese', 0)} scatole")
        
        col_qty, col_tipo = st.columns([1, 2])
        with col_qty:
            qty = st.number_input("Quantità", min_value=1, value=1)
        with col_tipo:
            tipo_mov = st.radio("Azione", ["Prelievo ➖", "Carico ➕"], horizontal=True)

        # Scadenza
        scadenza_str, dt_scadenza_yyyymm = "-", None
        if "Carico" in tipo_mov:
            c_m, c_a = st.columns(2)
            with c_m: mese = st.selectbox("Mese Scad.", range(1, 13))
            with c_a: anno = st.selectbox("Anno Scad.", range(datetime.now().year, datetime.now().year + 6))
            scadenza_str = f"{mese:02d}/{anno}"
            dt_scadenza_yyyymm = f"{anno}-{mese:02d}"

        if st.button("Registra Movimento", type="primary", use_container_width=True):
            if codice_art not in st.session_state['magazzino']:
                st.session_state['magazzino'][codice_art] = {'qty': 0, 'scadenze': []}
            
            dati = st.session_state['magazzino'][codice_art]
            
            if "Carico" in tipo_mov:
                dati['qty'] += qty
                dati['scadenze'].append({'data': dt_scadenza_yyyymm, 'qty_batch': qty, 'display': scadenza_str})
                dati['scadenze'].sort(key=lambda x: x['data'])
            else:
                if dati['qty'] < qty:
                    st.error("Giacenza insufficiente!")
                    st.stop()
                dati['qty'] -= qty
                # FIFO
                rem = qty
                new_scad = []
                for batch in dati['scadenze']:
                    if rem > 0:
                        if batch['qty_batch'] > rem:
                            batch['qty_batch'] -= rem
                            rem = 0
                            new_scad.append(batch)
                        else:
                            rem -= batch['qty_batch']
                    else:
                        new_scad.append(batch)
                dati['scadenze'] = new_scad

            st.session_state['storico'].insert(0, {
                "Data": datetime.now().strftime("%d/%m %H:%M"),
                "Prodotto": row_art['Descrizione'],
                "Azione": "➕" if "Carico" in tipo_mov else "➖",
                "Qta": qty,
                "Giacenza": dati['qty']
            })
            st.success("Fatto!")

    # === TAB 2: ORDINE BIMESTRALE ===
    with tab_ordini:
        st.markdown(f"""
        ### 📊 Calcolo Riordino per {INTERVALLO_CONTROLLO_MESI} Mesi
        Il sistema calcola quanto ordinare per coprire **{INTERVALLO_CONTROLLO_MESI} mesi** di lavoro + **2 settimane** di sicurezza.
        """)
        
        df_calc = df_master.copy()
        df_calc['Giacenza'] = df_calc['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
        
        # 1. Calcolo Consumo Mensile Reale (Kit)
        def get_monthly_consumption(row):
            # Priorità al calcolo sui Test se disponibili
            if row['Consumo_Test_Mese'] > 0 and row['Test_per_Scatola'] > 0:
                return row['Consumo_Test_Mese'] / row['Test_per_Scatola']
            return row['Fabbisogno_Kit_Mese']

        df_calc['Consumo_Mensile_Kit'] = df_calc.apply(get_monthly_consumption, axis=1)
        
        # 2. Calcolo Target (Obiettivo Scorta)
        # Esempio: Consumo 10/mese -> Target 2.5 mesi -> 25 scatole target
        df_calc['Target_Stock'] = df_calc['Consumo_Mensile_Kit'] * TARGET_MESI
        df_calc['Target_Stock'] = df_calc['Target_Stock'].apply(math.ceil) # Arrotonda per eccesso
        
        # 3. Calcolo Da Ordinare
        df_calc['Da_Ordinare'] = df_calc.apply(lambda x: max(0, x['Target_Stock'] - x['Giacenza']), axis=1)
        
        # 4. Copertura Attuale (in Mesi)
        def calc_coverage(row):
            if row['Consumo_Mensile_Kit'] <= 0: return 99
            return round(row['Giacenza'] / row['Consumo_Mensile_Kit'], 1)
            
        df_calc['Copertura_Mesi'] = df_calc.apply(calc_coverage, axis=1)
        
        # 5. Stato
        def get_status(row):
            if row['Consumo_Mensile_Kit'] == 0: return "⚪ Info"
            if row['Giacenza'] == 0: return "🔴 VUOTO"
            if row['Copertura_Mesi'] < LEAD_TIME_MESI: return "🟠 CRITICO"
            if row['Copertura_Mesi'] < INTERVALLO_CONTROLLO_MESI: return "🟡 DA REINTEGRARE"
            return "🟢 COPERTO"

        df_calc['Stato'] = df_calc.apply(get_status, axis=1)
        
        # Visualizzazione
        df_view = df_calc.sort_values(by='Copertura_Mesi')
        
        if st.checkbox("Nascondi prodotti già coperti (Verdi)", value=True):
            df_view = df_view[df_view['Stato'] != "🟢 COPERTO"]

        st.dataframe(
            df_view[['Stato', 'Descrizione', 'Giacenza', 'Consumo_Mensile_Kit', 'Target_Stock', 'Da_Ordinare']],
            use_container_width=True,
            column_config={
                "Consumo_Mensile_Kit": st.column_config.NumberColumn("Consumo/Mese", format="%.1f"),
                "Target_Stock": st.column_config.NumberColumn("Scorta Obiettivo", help=f"Quanto serve per {TARGET_MESI} mesi"),
                "Da_Ordinare": st.column_config.NumberColumn("🛒 DA ORDINARE", help="Differenza tra Obiettivo e Giacenza")
            }
        )
        
        # Export
        if st.button("📥 Scarica Ordine Excel"):
            df_out = df_calc[df_calc['Da_Ordinare'] > 0][['Codice', 'Descrizione', 'Da_Ordinare', 'Confezione']]
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_out.to_excel(writer, index=False)
            st.download_button("Download File", data=buffer.getvalue(), file_name="ordine_bimestrale.xlsx")

    # === TAB 3: SCADENZE ===
    with tab_scadenze:
        st.write("### 📅 Monitoraggio Scadenze")
        scad_list = []
        today_str = datetime.now().strftime("%Y-%m")
        limit_str = (datetime.now() + pd.DateOffset(months=INTERVALLO_CONTROLLO_MESI)).strftime("%Y-%m")
        
        for cod, data in st.session_state['magazzino'].items():
            for batch in data['scadenze']:
                # Mostra solo se scade entro il prossimo ciclo di controllo
                status = "🟢"
                if batch['data'] < today_str: status = "☠️ SCADUTO"
                elif batch['data'] <= limit_str: status = "⚠️ SCADE PRESTO"
                
                scad_list.append({
                    "Stato": status,
                    "Prodotto": df_master[df_master['Codice']==cod]['Descrizione'].iloc[0],
                    "Scatole": batch['qty_batch'],
                    "Scadenza": batch['display'],
                    "Sort": batch['data']
                })
        
        if scad_list:
            st.dataframe(pd.DataFrame(scad_list).sort_values(by='Sort'), use_container_width=True)
        else:
            st.info("Nessuna scadenza critica rilevata.")

else:
    st.error("Errore caricamento dati.")
