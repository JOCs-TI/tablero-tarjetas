# Tablero Ancora — Tarjetas de Crédito

Tablero Streamlit de gasto en tarjeta de crédito corporativa de Jorge Salas
Cacho (JSC) y Jorge Salas Benito (JSB), con identidad de marca Ancora.

## Vista

Una sola página: KPIs (total acumulado, movimientos, centros de costo),
gasto por año y persona, variación año contra año, resumen 80/20 por
producto, gasto por centro de costo, y gasto por oficina (mapa de árbol con
clic para entrar a cada oficina y ver su desglose por producto).

## Correr en local

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Datos

Lee en vivo un Google Sheet compartido como "Cualquiera con el link"
(solo lectura), descargando el libro completo vía `export?format=xlsx` cada
5 minutos — mismo patrón que `tablero-facturacion`, sin API key ni cuenta de
servicio.

## Confidencial

Contiene gasto de tarjeta de crédito por nombre de dos personas reales
(montos, oficinas, conceptos). Se publica así por decisión explícita del
usuario, ya que el plan gratis de Streamlit Cloud solo permite 1 app privada
(ocupada por `tablero-ancora`/Clientes).
