import streamlit as st
import pandas as pd
from datetime import datetime
import math
import io

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Abbott Manager ERP", layout="wide", initial_sidebar_state="collapsed")

# --- FUNZIONI DI CARICAMENTO E LOGICA ---
@st.cache_data
def load_master_data():
    try:
        # Carica il file Excel
        df = pd.read_excel('dati.xlsx', engine='openpyxl')
        
        # 1. Logica Intelligente per i Codici (Merge di due colonne)
        # Se 'LN ABBOTT' è vuoto, usa 'LN ABBOTT AGGIORNATI'
        if 'LN ABBOTT' in df.columns and 'LN ABBOTT AGGIORNATI' in df.columns:
            df['Codice_Finale'] = df['LN ABBOTT'].fillna(df['LN ABBOTT AGGIORNATI'])
        else:
            df['Codice_Finale'] = df.iloc[:, 4] # Fallback sulla 5a colonna se i nomi cambiano

        # 2. Mappatura Colonne basata sul tuo file
        col_map = {
            'Codice_Finale': 'Codice',
            'Descrizione commerciale': 'Descrizione',
            'Rgt/Cal/QC/Cons': 'Categoria',
            '# Kit/Mese': 'Fabbisogno_Mensile',
            'Conf.to': 'Confezione',
            'LOB': 'Reparto'
        }
        
        df = df.rename(columns=col_map)
        
        # 3. Pulizia
        df = df[df['Descrizione'].notna()] # Via le righe vuote
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False) # Pulisce codici numerici
        
        # Converte fabbisogno in numeri e gestisce errori
        df['Fabbisogno_Mensile'] = pd.to_numeric(df['Fabbisogno_Mensile'], errors='coerce').fillna(0)
        
        # Chiave di ricerca
        df['Prodotto_Label'] = df['Descrizione'] + " [" + df['Codice'] + "]"
        
        return df[['Codice', 'Descrizione', 'Categoria', 'Fabbisogno_Mensile', 'Confezione', 'Reparto', 'Prodotto_Label']]
    except Exception as e:
        st.error(f"Errore critico nel file Excel: {e}")
        return pd.DataFrame()

# --- GESTIONE MEMORIA (SESSION STATE) ---
if 'magazzino' not in st.session_state:
    # Struttura: { 'CODICE_PRODOTTO': {'qty': 10, 'scadenze': ['2026-05', '2026-08']} }
    st.session_state['magazzino'] = {} 

if 'storico' not in st.session_state:
    st.session_state['storico'] = []

# --- INTERFACCIA UTENTE ---
st.title("🏥 Gestione Magazzino & Ordini")

df_master = load_master_data()

if not df_master.empty:
    
    # Suddivisione in 3 Tab Operativi
    tab_mov, tab_ordini, tab_scadenze = st.tabs(["⚡ Movimenti Veloci", "📊 Ordini & Scorte", "⚠️ Scadenze"])

    # === TAB 1: MOVIMENTI ===
    with tab_mov:
        st.markdown("### Registra Entrata/Uscita")
        
        # Selezione Prodotto
        lista_prodotti = df_master['Prodotto_Label'].tolist()
        prodotto_scelto = st.selectbox("Cerca Prodotto:", lista_prodotti)
        
        # Recupera dati prodotto
        row_art = df_master[df_master['Prodotto_Label'] == prodotto_scelto].iloc[0]
        codice_art = row_art['Codice']
        
        col_qty, col_tipo = st.columns([1, 2])
        with col_qty:
            qty = st.number_input("Quantità (Scatole)", min_value=1, value=1, step=1)
        with col_tipo:
            tipo_mov = st.radio("Tipo Movimento", ["Prelievo (Scarico) ➖", "Carico (Merce Arrivata) ➕"], horizontal=True)

        # SEZIONE SCADENZA (Solo se carico)
        scadenza_str = "-"
        dt_scadenza_obj = None
        
        if "Carico" in tipo_mov:
            st.info("📅 Inserisci Scadenza (Mese/Anno)")
            c_mese, c_anno = st.columns(2)
            with c_mese:
                mese = st.selectbox("Mese", range(1, 13))
            with c_anno:
                anno_corrente = datetime.now().year
                anno = st.selectbox("Anno", range(anno_corrente, anno_corrente + 6))
            
            # Creiamo una data fittizia "Fine mese" per i calcoli
            scadenza_str = f"{mese:02d}/{anno}"
            # Usiamo il primo giorno del mese successivo per confronto sicuro o primo giorno mese
            dt_scadenza_obj = f"{anno}-{mese:02d}" 

        if st.button("CONFERMA MOVIMENTO", type="primary", use_container_width=True):
            # Inizializza prodotto se nuovo
            if codice_art not in st.session_state['magazzino']:
                st.session_state['magazzino'][codice_art] = {'qty': 0, 'scadenze': []}
            
            # Logica Aggiornamento
            dati_mag = st.session_state['magazzino'][codice_art]
            
            if "Carico" in tipo_mov:
                dati_mag['qty'] += qty
                # Aggiungo la scadenza N volte quante sono le scatole (per tracciarle singolarmente o a blocchi)
                # Per semplicità tracciamo il blocco.
                dati_mag['scadenze'].append({'data': dt_scadenza_obj, 'qty_batch': qty, 'display': scadenza_str})
                # Ordiniamo le scadenze dalla più vicina
                dati_mag['scadenze'].sort(key=lambda x: x['data'])
                
            else: # Prelievo
                # Controllo se c'è abbastanza merce
                if dati_mag['qty'] < qty:
                    st.error(f"Giacenza insufficiente! Hai solo {dati_mag['qty']} scatole.")
                    st.stop()
                else:
                    dati_mag['qty'] -= qty
                    # Logica FIFO (Scarico le scadenze più vecchie)
                    qty_to_remove = qty
                    new_scadenze = []
                    for batch in dati_mag['scadenze']:
                        if qty_to_remove > 0:
                            if batch['qty_batch'] > qty_to_remove:
                                batch['qty_batch'] -= qty_to_remove
                                qty_to_remove = 0
                                new_scadenze.append(batch)
                            else:
                                qty_to_remove -= batch['qty_batch']
                                # Il batch è finito, non lo aggiungo a new_scadenze
                        else:
                            new_scadenze.append(batch)
                    dati_mag['scadenze'] = new_scadenze

            # Aggiornamento Storico
            st.session_state['storico'].insert(0, {
                "Data": datetime.now().strftime("%d/%m %H:%M"),
                "Prodotto": row_art['Descrizione'],
                "Azione": "➕ Carico" if "Carico" in tipo_mov else "➖ Prelievo",
                "Qta": qty,
                "Giacenza Attuale": dati_mag['qty'],
                "Scadenza": scadenza_str
            })
            st.success(f"Registrato! Giacenza attuale: {dati_mag['qty']}")

    # === TAB 2: ORDINI INTELLIGENTI ===
    with tab_ordini:
        st.write("### 🛒 Calcolo Fabbisogno Mensile")
        
        # Creazione DataFrame Analisi
        df_calc = df_master.copy()
        
        # Mappa la giacenza attuale dal session_state
        def get_stock(cod):
            return st.session_state['magazzino'].get(cod, {}).get('qty', 0)
            
        df_calc['Giacenza'] = df_calc['Codice'].apply(get_stock)
        
        # Calcoli Matematici
        # Arrotondiamo per eccesso il fabbisogno (Es. 0.75 -> 1)
        df_calc['Fabbisogno_Rounded'] = df_calc['Fabbisogno_Mensile'].apply(math.ceil)
        
        # Calcolo DA ORDINARE
        # Se ho 2, me ne servono 10 -> Ordino 8. Se ho 12 -> Ordino 0.
        df_calc['DA_ORDINARE'] = df_calc.apply(
            lambda x: max(0, x['Fabbisogno_Rounded'] - x['Giacenza']), axis=1
        )
        
        # Calcolo COPERTURA (Giorni stimati)
        # Se consumo 10 al mese (0.33 al giorno) e ne ho 5 -> 15 giorni
        def calcola_giorni(row):
            if row['Fabbisogno_Mensile'] <= 0: return 999
            consumo_giornaliero = row['Fabbisogno_Mensile'] / 30
            giorni = row['Giacenza'] / consumo_giornaliero
            return int(giorni)

        df_calc['Autonomia_Giorni'] = df_calc.apply(calcola_giorni, axis=1)

        # Logica SEMAFORO
        def get_semaforo(row):
            if row['Fabbisogno_Mensile'] == 0: return "⚪ Info"
            if row['Giacenza'] == 0: return "🔴 ESAURITO"
            if row['Autonomia_Giorni'] < 7: return "🟠 URGENTE (<7gg)"
            if row['Autonomia_Giorni'] < 20: return "🟡 ORDINE PREVISTO"
            return "🟢 OK"

        df_calc['Stato'] = df_calc.apply(get_semaforo, axis=1)
        
        # Ordina per urgenza
        df_view = df_calc.sort_values(by=['Autonomia_Giorni'])[['Stato', 'Descrizione', 'Giacenza', 'Fabbisogno_Rounded', 'DA_ORDINARE', 'Autonomia_Giorni']]
        
        # Visualizza solo quelli rilevanti (nascondi i verdi se vuoi)
        filter_ok = st.checkbox("Nascondi prodotti OK (Verdi)", value=True)
        if filter_ok:
            df_view = df_view[df_view['Stato'] != "🟢 OK"]

        st.dataframe(df_view, use_container_width=True)
        
        # Bottone Export
        ordine_export = df_calc[df_calc['DA_ORDINARE'] > 0][['Codice', 'Descrizione', 'DA_ORDINARE', 'Confezione']]
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            ordine_export.to_excel(writer, index=False)
            
        st.download_button("📥 Scarica Lista Ordine (Excel)", data=buffer.getvalue(), file_name="ordine_magazzino.xlsx")

    # === TAB 3: ANALISI SCADENZE ===
    with tab_scadenze:
        st.write("### 🗓️ Controllo Scadenze")
        
        # Costruiamo la lista di tutto ciò che scade
        allarmi_scadenza = []
        oggi_str = datetime.now().strftime("%Y-%m") # es. 2024-02
        
        for cod, data in st.session_state['magazzino'].items():
            if data['qty'] > 0:
                for batch in data['scadenze']:
                    # Confronto date (Stringa YYYY-MM)
                    if batch['data'] <= today_plus_3_months = (datetime.now() + pd.DateOffset(months=3)).strftime("%Y-%m"): # Pseudocodice logica visuale
                        # Calcolo semplice stato
                        stato_scad = "🟢"
                        if batch['data'] < oggi_str: stato_scad = "☠️ SCADUTO"
                        elif batch['data'] == oggi_str: stato_scad = "🔴 SCADE ORA"
                        else: stato_scad = "🟡 SCADE PRESTO"
                        
                        nome = df_master[df_master['Codice'] == cod]['Descrizione'].iloc[0]
                        allarmi_scadenza.append({
                            "Stato": stato_scad,
                            "Prodotto": nome,
                            "Scatole": batch['qty_batch'],
                            "Scadenza": batch['display']
                        })
        
        if allarmi_scadenza:
            df_scad = pd.DataFrame(allarmi_scadenza)
            df_scad = df_scad.sort_values(by='Scadenza')
            st.dataframe(df_scad, use_container_width=True)
        else:
            st.info("Nessun prodotto con scadenza critica registrato al momento.")

else:
    st.error("Errore: Il file dati.xlsx non è stato caricato correttamente o è vuoto.")

# Sidebar per Reset
with st.sidebar:
    st.write("🔧 Opzioni")
    if st.button("🗑️ Reset Magazzino"):
        st.session_state['magazzino'] = {}
        st.session_state['storico'] = []
        st.rerun()
