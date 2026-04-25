# StockAnalyzer - Manual tecnico y de usuario

Sistema web para analizar acciones, detectar oportunidades de swing trading, validar planes de entrada con reglas de riesgo y medir el rendimiento por paper trading.

Este documento esta escrito para explicar el proyecto a otras personas: que hace el software, como se usa, que estrategias sigue, como valida una operacion y que significan sus resultados.

---

## Indice

1. Vision general del proyecto
2. Problema que resuelve
3. Arquitectura del software
4. Flujo completo de trabajo
5. Modulos principales
6. Datos que almacena la aplicacion
7. Motor de analisis tecnico
8. Estrategias detectadas
9. Motor de validacion de operaciones
10. Sistema de puntuacion
11. Generacion de planes de trading
12. Gestion del riesgo y tamano de posicion
13. Paper trading y seguimiento de operaciones
14. Metricas de rendimiento
15. Dashboard y funciones de usuario
16. Informacion fundamental, analistas y smart money
17. Correos y reportes
18. Comandos de administracion
19. Ejemplos practicos
20. Reglas importantes y limitaciones
21. Glosario

---

## Capitulo 1. Vision general del proyecto

StockAnalyzer es una aplicacion Django orientada a swing trading en acciones. El sistema permite que un usuario cree una lista de acciones, ejecute analisis tecnico, reciba senales clasificadas como `BUY`, `WATCH` o `NO_BUY`, genere planes de trading y valide si esos planes tienen suficiente calidad antes de considerarlos operables.

La aplicacion no solo busca senales tecnicas. Tambien revisa contexto de mercado, estructura alcista, volumen, soporte, resistencia, ratio riesgo/beneficio y calidad del setup. Despues, puede simular la vida de la operacion en paper trading para medir si la estrategia funciono.

En terminos simples:

- El usuario agrega tickers como `AAPL`, `MSFT`, `NVDA`.
- El sistema descarga precios diarios OHLCV desde Yahoo Finance usando `yfinance`.
- Calcula indicadores tecnicos como SMA9, SMA20, SMA50, SMA200, RSI y volumen promedio.
- Detecta estrategias de entrada.
- Calcula entrada, stop, target y R:R.
- Valida la operacion con un motor profesional.
- Genera un plan de trading por usuario.
- Hace seguimiento del resultado en papel.
- Presenta metricas de rendimiento.

---

## Capitulo 2. Problema que resuelve

El problema principal es que muchas operaciones se toman por intuicion o por una senal aislada. Por ejemplo: "rompio la media de 50 dias, compro". El proyecto intenta convertir ese proceso en un flujo mas objetivo:

1. Detectar setups tecnicos repetibles.
2. Rechazar operaciones cuando el mercado general esta debil.
3. Evitar entradas demasiado extendidas.
4. Exigir que el stop tenga sentido tecnico.
5. Exigir que el target tenga espacio real antes de resistencia.
6. Medir si el ratio riesgo/beneficio compensa.
7. Registrar el resultado de cada plan.

El software funciona como un filtro. No busca operar todo. Su objetivo es reducir operaciones malas y separar las oportunidades en tres grupos:

| Estado | Significado |
|---|---|
| `VALID` | El plan cumple todas las reglas principales. Puede considerarse para compra. |
| `WATCHLIST` | Tiene 1 o 2 problemas. Se debe esperar o monitorear. |
| `INVALID` | Tiene 3 o mas problemas, o falla un filtro critico. No se debe operar. |

---

## Capitulo 3. Arquitectura del software

La aplicacion esta organizada como un proyecto Django:

```text
stockanalyzer/        Configuracion principal Django
users/                Registro, login y perfil de usuario
stocks/               Modelos, vistas, formularios y logica de acciones
stocks/services/      Servicios de validacion, planes, smart money y rendimiento
templates/            Paginas HTML
static/               Archivos estaticos
manage.py             Punto de entrada de comandos Django
MANUAL.md             Este documento
```

El flujo tecnico principal es:

```text
Usuario
  -> Dashboard
  -> Watchlist
  -> run_analysis_for_ticker()
  -> fetch_and_store_prices()
  -> compute_indicators()
  -> estrategias tecnicas
  -> compute_trading_levels()
  -> generate_trade_plan()
  -> validate_trade()
  -> Trade
  -> update_trades
  -> Performance
```

Tecnologias usadas:

| Tecnologia | Uso |
|---|---|
| Django 4.2 | Backend, vistas, modelos, formularios y autenticacion |
| SQLite | Base de datos local por defecto |
| yfinance | Descarga de precios, informacion de compania, analistas e institucionales |
| pandas | Procesamiento de series historicas |
| ta | Indicadores tecnicos como RSI |
| Tailwind CSS | Estilos de interfaz |
| ApexCharts | Graficas de rendimiento |

---

## Capitulo 4. Flujo completo de trabajo

### 4.1 Registro e inicio de sesion

El usuario crea una cuenta con nombre de usuario, email y contrasena. Despues puede entrar al dashboard. Cada usuario tiene su propia watchlist, listas y perfil de riesgo.

### 4.2 Creacion de watchlist

El usuario agrega uno o varios tickers separados por coma:

```text
AAPL, MSFT, NVDA, AMD
```

El sistema valida que los simbolos:

- No esten vacios.
- Sean alfabeticos.
- No tengan mas de 10 caracteres.
- Se conviertan a mayusculas.

Ejemplo:

```text
Entrada del usuario: aapl, msft
Resultado guardado: AAPL, MSFT
```

### 4.3 Analisis

El usuario puede analizar una accion individual o ejecutar `Analyze All`. El analisis:

- Descarga o actualiza precios.
- Calcula indicadores.
- Detecta estrategias.
- Calcula score inicial.
- Guarda un `TickerAnalysis`.
- Genera planes de trading con validacion.

### 4.4 Trade Plans

La pantalla de planes muestra la ultima operacion generada para cada ticker del usuario. Permite ver:

- Entrada.
- Stop.
- Target.
- Tamano de posicion.
- Riesgo en dolares.
- Recompensa estimada.
- R:R.
- Estado de validacion.
- Razones aprobadas y fallidas.
- Soporte, resistencia, volumen relativo y desglose del score.

### 4.5 Performance

La pantalla de rendimiento muestra como se comportaron las operaciones simuladas:

- Pendientes.
- Activas.
- Cerradas.
- Win rate.
- Profit factor.
- Expectancy.
- Equity curve.
- Rendimiento por estrategia, volumen relativo y R:R.

---

## Capitulo 5. Modulos principales

### 5.1 `stocks/analysis.py`

Contiene el motor de analisis tecnico. Sus responsabilidades:

- Descargar precios con `yfinance`.
- Guardar OHLCV en la base de datos.
- Construir DataFrames desde la base de datos.
- Calcular indicadores.
- Detectar estrategias.
- Calcular score inicial.
- Calcular niveles de entrada, stop y target.
- Crear o actualizar `TickerAnalysis`.

Funcion principal:

```python
run_analysis_for_ticker(ticker, force=False)
```

### 5.2 `stocks/services/validator.py`

Contiene el motor profesional de validacion. Es el modulo clave para decidir si un plan es operable, vigilable o rechazado.

Funcion principal:

```python
validate_trade(ticker, entry, stop, target, strategies, df, spy_data)
```

### 5.3 `stocks/services/trade_engine.py`

Convierte un analisis tecnico en un plan de trading por usuario. Usa el perfil de riesgo para calcular acciones y guarda el `Trade`.

Funcion principal:

```python
generate_trade_plan(user, ticker, analysis)
```

### 5.4 `stocks/services/trade_tracker.py`

Simula operaciones en papel:

- Activa una operacion si el precio toca la entrada.
- Cierra una operacion si toca stop, target o llega al limite de tiempo.

Funciones principales:

```python
activate_pending_trades()
update_active_trades()
```

### 5.5 `stocks/services/performance.py`

Calcula metricas de rendimiento:

- Win rate.
- Profit factor.
- Expectancy.
- R:R promedio.
- Dias promedio.
- PnL total.
- Equity curve.
- Desglose por estrategia, RVOL y R:R.

### 5.6 `stocks/services/smart_money.py`

Obtiene informacion institucional e insiders usando `yfinance`:

- Institutional holders.
- Insider transactions.
- Direccion: compra, venta, neutral u otro.

---

## Capitulo 6. Datos que almacena la aplicacion

### 6.1 `Ticker`

Representa un simbolo de accion. Es compartido entre usuarios. Si dos usuarios siguen `AAPL`, el ticker se guarda una sola vez.

Campos importantes:

- `symbol`
- `last_price_update`

### 6.2 `TickerPrice`

Guarda precios diarios:

- Fecha.
- Open.
- High.
- Low.
- Close.
- Volume.

Cada ticker solo puede tener un registro por fecha.

### 6.3 `TickerAnalysis`

Guarda el resultado del analisis tecnico por ticker y fecha:

- `signal`: `BUY`, `WATCH`, `NO_BUY`.
- `confidence_score`.
- `current_price`.
- `entry_price`.
- `stop_loss`.
- `take_profit`.
- `risk_reward_ratio`.
- `strategies_triggered`.
- `sma9_data`.
- `market_structure`.
- `explanation`.

### 6.4 `TradingProfile`

Define la cuenta del usuario:

- Capital de cuenta.
- Porcentaje de riesgo por operacion.
- Si desea recibir email despues de analizar todo.

Ejemplo:

```text
Cuenta: $10,000
Riesgo por trade: 1%
Riesgo permitido: $100
```

### 6.5 `Watchlist`

Relacion entre usuario y ticker. Permite que cada usuario tenga su propia lista.

### 6.6 `StockList`

Listas personalizadas como:

```text
Swing
Tech
Finanzas
Semiconductores
```

### 6.7 `Trade`

Plan de trading generado para un usuario:

- Estrategia.
- Entrada.
- Stop.
- Target.
- Acciones.
- Riesgo.
- Recompensa.
- R:R.
- Estado de validacion.
- Razones de aprobacion y rechazo.
- Soporte y resistencia.
- Volumen relativo.
- Estado de paper trading.
- Resultado final.
- PnL.

---

## Capitulo 7. Motor de analisis tecnico

El analisis tecnico empieza con precios historicos. Si el ticker ya tiene al menos 400 registros, se descargan solo los ultimos 30 dias. Si tiene pocos datos, se descargan 2 anos.

Despues se calculan:

| Indicador | Descripcion |
|---|---|
| `sma9` | Media movil simple de 9 dias |
| `sma20` | Media movil simple de 20 dias |
| `sma50` | Media movil simple de 50 dias |
| `sma200` | Media movil simple de 200 dias |
| `rsi` | RSI de 14 periodos |
| `avg_vol20` | Volumen promedio de 20 dias |

Ejemplo de fila tecnica:

```text
Ticker: AAPL
Close: 182.50
SMA9: 180.20
SMA20: 177.80
SMA50: 174.30
SMA200: 161.40
RSI: 57.8
Volumen: 65,000,000
Avg Vol 20: 58,000,000
```

Despues se aplican estrategias. Cada estrategia puede activar una etiqueta en `strategies_triggered`.

La senal inicial se decide por score:

| Score | Senal |
|---|---|
| 70 a 100 | `BUY` |
| 40 a 69 | `WATCH` |
| 0 a 39 | `NO_BUY` |

Esta senal inicial no es la validacion final. La validacion final ocurre en `validator.py`.

---

## Capitulo 8. Estrategias detectadas

El sistema detecta estrategias de entrada en `stocks/analysis.py`. Una accion puede activar varias estrategias el mismo dia.

### 8.1 Sweet Spot

Busca una zona ideal de swing trading entre SMA200 y SMA50.

Condiciones:

- Precio actual por encima de SMA200.
- Precio actual menor o igual a SMA50 + 3%.
- SMA200 menor que SMA50.
- RSI entre 35 y 60.
- Volumen actual al menos 80% del promedio de 20 dias.

Ejemplo:

```text
SMA200: 100
SMA50: 115
Precio: 108
RSI: 48
Volumen actual: 900,000
Volumen promedio 20d: 1,000,000

Resultado: Sweet Spot activo
```

Interpretacion: el precio esta cerca de soporte de largo plazo, pero todavia tiene espacio hasta la SMA50.

### 8.2 SMA200 Bounce

Detecta cuando el precio cayo bajo SMA200 recientemente y ya recupero esa media.

Condiciones:

- Precio actual por encima de SMA200.
- En los ultimos 10 dias hubo al menos un cierre bajo SMA200.
- Precio actual no esta mas de 5% sobre SMA50.
- RSI menor o igual a 65.

Ejemplo:

```text
Hace 4 dias: close 98, SMA200 100
Hoy: close 103, SMA200 100, SMA50 108
RSI: 51

Resultado: SMA200 Bounce activo
```

Interpretacion: hubo una perdida temporal de la media larga y luego recuperacion.

### 8.3 Fresh SMA50 Breakout

Detecta rupturas recientes de SMA50 con volumen.

Condiciones:

- En los ultimos 3 dias el precio cruzo desde abajo hacia arriba de SMA50.
- El dia de la ruptura tuvo volumen mayor al promedio de 20 dias.
- RSI actual no supera 65.

Ejemplo:

```text
Ayer:
Close anterior: 49
SMA50 anterior: 50
Close actual: 52
SMA50 actual: 50.5
Volumen actual: 1.4x promedio

Resultado: Fresh SMA50 Breakout activo
```

Interpretacion: es una ruptura fresca, no una entrada tarde despues de muchos dias de subida.

### 8.4 RSI Rebound

Detecta recuperacion desde sobreventa.

Condiciones:

- Precio no esta debajo de SMA200.
- En los ultimos 14 dias el RSI estuvo bajo 30.
- RSI actual mayor que 40.
- RSI actual no mayor que 65.

Ejemplo:

```text
RSI minimo reciente: 27
RSI actual: 44
Precio: 72
SMA200: 68

Resultado: RSI Rebound activo
```

Interpretacion: hubo panico vendedor y luego recuperacion.

### 8.5 Capitulation + Reversal

Detecta una venta fuerte con volumen extremo seguida de recuperacion.

Condiciones:

- En los ultimos 7 dias hubo una caida mayor al 5%.
- Ese dia el volumen fue mayor a 1.5 veces el promedio.
- El dia siguiente cerro por encima del cierre del dia de caida.
- El precio actual no esta mas de 3% por debajo de SMA200.

Ejemplo:

```text
Dia 1: close cae -6.8%, volumen 1.9x promedio
Dia 2: close sube respecto al dia 1
Precio actual: cerca o sobre SMA200

Resultado: Capitulation + Reversal activo
```

Interpretacion: se detecta capitulacion vendedora y reaccion compradora.

### 8.6 SMA9 Pullback

Detecta pullbacks cortos dentro de una tendencia alcista fuerte.

La estrategia devuelve un diccionario con:

```python
{
    "trend": True,
    "pullback": True,
    "confirmation": True,
    "valid": True
}
```

Condiciones:

- Tendencia: close > SMA20 y SMA9 > SMA20.
- Estructura reciente: al menos 50% de las ultimas 20 velas muestran maximos y minimos crecientes.
- Pullback: el precio esta dentro de +/-1.5% de SMA9.
- Confirmacion: vela actual alcista, es decir, close > open.

Ejemplo:

```text
Close: 51.20
SMA9: 50.90
SMA20: 48.70
Vela actual: close > open
Estructura: mas de 50% de highs/lows crecientes

Resultado: SMA9 Pullback valido
```

---

## Capitulo 9. Motor de validacion de operaciones

El archivo `stocks/services/validator.py` contiene el motor de validacion profesional. Su objetivo es revisar si un plan de trade tiene calidad suficiente para ser ejecutado.

La validacion evalua 6 dimensiones:

1. Tendencia de mercado.
2. Estructura de mercado.
3. RSI.
4. Volumen.
5. Soporte y resistencia.
6. Contexto macro del SPY.

Tambien revisa:

- Ratio riesgo/beneficio.
- Stop contra soporte.
- Target contra resistencia.
- Tipo de estrategia.
- Penalizaciones severas.
- Estado final.

### 9.1 Tipos de estrategia en el validador

El validador reduce las estrategias a dos tipos:

| Tipo | Cuando se usa |
|---|---|
| `breakout` | Si aparece `Fresh SMA50 Breakout` |
| `pullback` | Para todos los demas casos |

Esto se hace en:

```python
classify_strategy(strategies, df)
```

Ejemplo:

```text
strategies = ["Fresh SMA50 Breakout"]
Resultado: breakout

strategies = ["Sweet Spot", "RSI Rebound"]
Resultado: pullback
```

### 9.2 Por que solo dos tipos

Aunque el analisis puede detectar varias estrategias, la validacion de riesgo se simplifica:

- Breakout: requiere confirmacion de volumen mas fuerte y mejor R:R.
- Pullback: busca comprar cerca de soporte; puede aceptar un R:R minimo menor.

Minimos:

| Tipo | R:R minimo |
|---|---|
| Pullback | 1.7 |
| Breakout | 2.0 |

### 9.3 Deteccion de pivotes

El validador usa pivotes para detectar swing highs y swing lows confirmados.

Parametros:

```python
PIVOT_WINDOW = 5
MIN_PIVOTS = 3
```

Un pivot high se confirma si el high de una vela es mayor que los 5 highs anteriores y los 5 highs posteriores.

Un pivot low se confirma si el low de una vela es menor que los 5 lows anteriores y los 5 lows posteriores.

Ejemplo conceptual:

```text
Highs: 10, 11, 12, 15, 13, 12, 11
El 15 puede ser pivot high porque supera a los highs cercanos.
```

Funciones:

```python
_find_pivot_highs(df)
_find_pivot_lows(df)
```

### 9.4 Estructura alcista

La funcion:

```python
is_bullish_structure(df)
```

exige que los ultimos 3 pivot highs y los ultimos 3 pivot lows confirmen:

- Higher Highs: cada maximo es mayor que el anterior.
- Higher Lows: cada minimo es mayor que el anterior.

Ejemplo valido:

```text
Pivot highs: 100, 108, 115
Pivot lows:  90,  95, 101

Resultado: estructura alcista confirmada
```

Ejemplo invalido:

```text
Pivot highs: 100, 108, 103
Pivot lows:  90,  95,  91

Resultado: estructura debil
```

Razones que puede devolver:

- `Higher highs and higher lows confirmed`
- `Higher highs confirmed but lows are not rising`
- `Higher lows confirmed but highs are not rising`
- `No clear higher highs or higher lows - structure is weak`
- `Only X swing high(s) found - need 3+`
- `Only X swing low(s) found - need 3+`

### 9.5 Soporte y resistencia

El validador busca:

- Resistencia: pivot high confirmado al menos 1% por encima de la entrada.
- Soporte: pivot low confirmado al menos 1% por debajo de la entrada.

Funciones:

```python
find_nearest_resistance(pivot_highs, price, buffer=0.01)
find_nearest_support(pivot_lows, price, buffer=0.01)
```

Ejemplo:

```text
Entrada: 100
Pivot highs: 103, 111, 125
Resistencia cercana: 103

Pivot lows: 96, 88, 80
Soporte cercano: 96
```

### 9.6 Ajuste del target

Si el target esta por encima de una resistencia cercana, el sistema lo baja para quedar debajo de la resistencia:

```text
Target original: 120
Resistencia: 115
Target ajustado: 115 * 0.993 = 114.20
```

Esto evita que el plan asuma que el precio atravesara una resistencia sin confirmacion.

### 9.7 Validacion del stop

Si hay soporte:

- El stop debe estar por debajo del soporte.
- Pero no demasiado lejos.

Reglas:

```text
Si stop > soporte:
    falla porque el stop no esta protegido por soporte.

Si stop < soporte * 0.94:
    falla porque el riesgo es demasiado amplio, mas de 6% bajo soporte.

Si soporte * 0.94 <= stop <= soporte:
    pasa porque el stop queda protegido y razonable.
```

Ejemplo valido:

```text
Soporte: 95
Stop: 93
Resultado: stop protegido bajo soporte
```

Ejemplo invalido:

```text
Soporte: 95
Stop: 97
Resultado: stop encima del soporte, no protegido
```

Ejemplo de riesgo excesivo:

```text
Soporte: 95
Stop: 87
Resultado: stop demasiado lejos, riesgo amplio
```

### 9.8 Filtro macro SPY

El SPY funciona como filtro de salud del mercado. Si SPY esta por debajo de SMA200, el validador bloquea las compras.

Regla dura:

```text
SPY below SMA200 -> INVALID
```

Ejemplo:

```text
SPY close: 420
SPY SMA200: 435
Resultado: mercado bajista, operaciones BUY bloqueadas
```

Si SPY esta por encima de SMA200 pero por debajo de SMA50, no bloquea totalmente, pero penaliza el score.

### 9.9 Validacion de volumen

El volumen relativo se calcula asi:

```text
RVOL = volumen actual / volumen promedio de 20 dias
```

Clasificacion:

| RVOL | Calidad |
|---|---|
| >= 1.2 | Strong |
| >= 1.0 y < 1.2 | Neutral |
| < 1.0 | Weak |

Regla especial:

- En `breakout`, RVOL debe ser al menos 1.2.
- En `pullback`, RVOL bajo es una falla, pero se trata como menos critico que en breakout.

Ejemplo:

```text
Volumen actual: 1,500,000
Promedio 20d: 1,000,000
RVOL: 1.50
Calidad: Strong
```

### 9.10 Determinacion de estado

El estado final depende de la cantidad de fallas:

| Fallas | Estado | Significado |
|---|---|---|
| 0 | `VALID` | Puede considerarse operable |
| 1-2 | `WATCHLIST` | Esperar, monitorear o ajustar |
| 3+ | `INVALID` | Rechazar |

Excepcion importante: si SPY esta debajo de SMA200, la operacion queda `INVALID` directamente.

---

## Capitulo 10. Sistema de puntuacion

Existen dos scores:

1. Score inicial de analisis tecnico (`compute_score`).
2. Score validado del plan (`compute_validated_score`).

### 10.1 Score inicial

Se calcula antes de validar el plan. Parte de las estrategias detectadas:

- Cada estrategia suma 25 puntos.
- Volumen fuerte puede sumar 5.
- Varias estrategias suman bonus.
- SMA9 pullback puede sumar bonus.
- RSI sobrecomprado resta.
- Precio muy extendido sobre SMA50 resta.
- Precio bajo SMA200 resta.
- Estructura de mercado alcista suma; debil resta.
- SPY bajo SMA200 resta.

Ejemplo:

```text
Estrategias detectadas: Sweet Spot, RSI Rebound
Base: 2 * 25 = 50
Bonus por varias estrategias: +10
Estructura alcista: +10
Score aproximado: 70
Senal: BUY
```

### 10.2 Score validado

El validador usa 6 componentes:

| Componente | Maximo |
|---|---:|
| Tendencia | 20 |
| Estructura | 20 |
| RSI | 15 |
| Volumen | 15 |
| Soporte/Resistencia | 20 |
| SPY | 10 |
| Total | 100 |

### 10.3 Puntos por tendencia

La tendencia puede sumar hasta 20:

| Regla | Puntos |
|---|---:|
| Close > SMA200 | 8 |
| SMA50 > SMA200 | 7 |
| SMA20 > SMA50 | 3 |
| SMA9 > SMA20 | 2 |

Ejemplo:

```text
Close > SMA200: si (+8)
SMA50 > SMA200: si (+7)
SMA20 > SMA50: no (+0)
SMA9 > SMA20: si (+2)
Trend score: 17/20
```

### 10.4 Puntos por RSI

| RSI | Puntos |
|---|---:|
| 45 a 60 | 15 |
| 40 a 65 | 10 |
| 35 a 70 | 5 |
| Fuera de esos rangos | 0 |

Interpretacion: el mejor RSI para swing trading es recuperacion sin sobrecompra.

### 10.5 Puntos por volumen

| RVOL | Puntos |
|---|---:|
| >= 1.2 | 15 |
| >= 1.0 | 8 |
| < 1.0 | 0 |

### 10.6 Puntos por soporte/resistencia y R:R

| R:R | Base S/R |
|---|---:|
| >= 2.5 | 20 |
| >= 2.0 | 15 |
| >= 1.7 | 10 |
| < 1.7 | 0 |

Luego puede sumar hasta el maximo de 20:

- +5 si hay soporte.
- +5 si hay resistencia.

### 10.7 Penalizaciones multiplicativas

Despues de calcular el score base, se aplica un factor de penalizacion:

| Problema | Penalizacion |
|---|---:|
| Sin estructura alcista | -0.40 |
| SPY sobre SMA200 pero bajo SMA50 | -0.20 |
| R:R criticamente bajo (< 1.0) | -0.50 |
| R:R bajo minimo de estrategia | -0.30 |
| RVOL menor a 0.6 | -0.30 |
| RVOL menor a 0.8 | -0.15 |
| Target a menos de 1% de entrada | -0.40 |

El factor nunca baja de 0.20 para evitar colapsar el calculo a cero salvo bloqueos duros.

Ejemplo:

```text
Base score: 80
Sin estructura alcista: -0.40
R:R bajo minimo: -0.30
Factor final: 0.30
Score final: 80 * 0.30 = 24
```

### 10.8 Ajuste por estado

El score final se ajusta segun el estado:

| Estado | Rango final |
|---|---|
| `VALID` | minimo 70 |
| `WATCHLIST` | entre 40 y 70 |
| `INVALID` | maximo 39 |

Esto mantiene coherencia entre la etiqueta y la puntuacion.

---

## Capitulo 11. Generacion de planes de trading

El analisis tecnico calcula niveles iniciales usando `compute_trading_levels`.

### 11.1 Si hay SMA9 Pullback valido

Reglas:

```text
Entrada = high de hoy * 1.002
Stop = minimo de los ultimos 10 dias * 0.995
Target = entrada + riesgo * 2.0
```

Ejemplo:

```text
High de hoy: 50.00
Entrada: 50.10
Swing low 10d: 47.00
Stop: 46.77
Riesgo: 3.33
Target: 56.76
R:R: 2.0
```

### 11.2 Si hay Sweet Spot

Reglas:

```text
Entrada = close * 1.005
Stop = max(SMA200 * 0.99, close * 0.93)
Target = SMA50 * 1.06
```

Ejemplo:

```text
Close: 100
SMA200: 94
SMA50: 110
Entrada: 100.50
Stop: max(93.06, 93.00) = 93.06
Target: 116.60
```

### 11.3 Si hay SMA200 Bounce

Reglas:

```text
Entrada = close * 1.005
Stop = SMA200 * 0.98
Target = SMA50 * 1.04
```

### 11.4 Otros casos

Si hay varias estrategias, se usa una entrada mas cercana:

```text
Entrada = close * 1.003
```

Si hay `Fresh SMA50 Breakout`:

```text
Entrada = close * 1.005
```

Si el precio esta bajo SMA50:

```text
Entrada = SMA50 * 1.002
```

Si el precio esta bajo SMA200:

```text
Entrada = SMA200 * 1.002
```

En casos generales:

```text
Entrada = close * 1.01
Stop = entrada * (1 - stop_pct)
Target = entrada + riesgo * 2.5
```

`stop_pct` es:

- 5% si hay varias estrategias.
- 7% si hay una sola estrategia.

---

## Capitulo 12. Gestion del riesgo y tamano de posicion

El tamano de posicion se calcula desde el perfil del usuario.

Formula:

```text
Riesgo en dolares = account_size * risk_per_trade_pct / 100
Riesgo por accion = entry - stop
Acciones = int(riesgo en dolares / riesgo por accion)
```

Ejemplo:

```text
Cuenta: $10,000
Riesgo por trade: 1%
Riesgo permitido: $100

Entrada: $50
Stop: $47.50
Riesgo por accion: $2.50

Acciones: 100 / 2.50 = 40
Riesgo total: 40 * 2.50 = $100
```

Recompensa:

```text
Reward = (target - entry) * acciones
```

Ejemplo:

```text
Target: $55
Entrada: $50
Acciones: 40
Reward: (55 - 50) * 40 = $200
R:R: 200 / 100 = 2.0
```

El sistema siempre asigna al menos 1 accion si el riesgo por accion es positivo.

---

## Capitulo 13. Paper trading y seguimiento de operaciones

El sistema simula operaciones sin ejecutar ordenes reales.

Estados:

| Estado | Significado |
|---|---|
| `PENDING` | Plan valido, esperando que el precio toque la entrada |
| `ACTIVE` | La entrada fue tocada por el rango diario |
| `CLOSED` | La operacion cerro por stop, target o tiempo |

### 13.1 Activacion

Una operacion se activa cuando el precio de entrada queda dentro del rango diario:

```text
Si low <= entry <= high:
    PENDING -> ACTIVE
```

Ejemplo:

```text
Entry: 100
Low del dia: 98
High del dia: 101

Resultado: operacion activada
```

### 13.2 Cierre

Una operacion activa se cierra cuando ocurre alguna condicion:

1. Toca stop.
2. Toca target.
3. Pasan 15 dias.

Regla conservadora: si el mismo dia toca stop y target, se toma primero el stop. Esto evita sobreestimar resultados.

```text
Prioridad:
1. Stop
2. Target
3. Tiempo maximo
```

### 13.3 Cierre por tiempo

Si pasan 15 dias y no se toco stop ni target:

- Si el precio esta al menos 0.10R por encima de entrada: `WIN`.
- Si esta al menos 0.10R por debajo de entrada: `LOSS`.
- Si esta cerca de entrada: `BREAKEVEN`.

Ejemplo:

```text
Entrada: 100
Stop: 95
Riesgo: 5
0.10R: 0.50

Salida dia 15 en 100.70 -> WIN
Salida dia 15 en 99.40 -> LOSS
Salida dia 15 en 100.20 -> BREAKEVEN
```

### 13.4 Metricas guardadas al cerrar

Cuando una operacion cierra, se guardan:

- Outcome: `WIN`, `LOSS`, `BREAKEVEN`.
- Exit date.
- Exit price.
- PnL dollars.
- PnL percent.
- Max profit durante la operacion.
- Max drawdown durante la operacion.
- Days held.

---

## Capitulo 14. Metricas de rendimiento

El modulo `performance.py` calcula metricas usando operaciones cerradas.

### 14.1 Win rate

```text
Win rate = wins / total closed trades * 100
```

Ejemplo:

```text
10 operaciones cerradas
6 wins
Win rate = 60%
```

### 14.2 Profit factor

```text
Profit factor = ganancias totales / perdidas absolutas totales
```

Ejemplo:

```text
Ganancias: $900
Perdidas: $450
Profit factor: 2.0
```

### 14.3 Expectancy

```text
Expectancy = win_rate * avg_win - loss_rate * avg_loss
```

Ejemplo:

```text
Win rate: 60%
Avg win: $200
Loss rate: 40%
Avg loss: $100

Expectancy = 0.60 * 200 - 0.40 * 100 = $80
```

Interpretacion: en promedio, cada trade cerrado aporta $80.

### 14.4 Equity curve

Simula el balance de la cuenta sumando o restando el PnL de cada trade cerrado en orden de fecha.

Ejemplo:

```text
Capital inicial: $10,000
Trade 1: +$150 -> $10,150
Trade 2: -$100 -> $10,050
Trade 3: +$220 -> $10,270
```

### 14.5 Desgloses

La aplicacion agrupa resultados por:

- Tipo de estrategia.
- Bucket de RVOL.
- Bucket de R:R.

Buckets de RVOL:

| RVOL | Bucket |
|---|---|
| >= 1.2 | Strong |
| 1.0 a 1.2 | Neutral |
| < 1.0 | Weak |

Buckets de R:R:

| R:R | Bucket |
|---|---|
| >= 2.5 | Excellent |
| 2.0 a 2.5 | Good |
| 1.7 a 2.0 | Minimum |
| < 1.7 | Below min |

---

## Capitulo 15. Dashboard y funciones de usuario

### 15.1 Dashboard

El dashboard muestra la watchlist del usuario. Permite:

- Ver ticker.
- Ver senal.
- Ver score.
- Filtrar por lista.
- Filtrar por senal.
- Buscar por prefijo de ticker.
- Ejecutar analisis individual.
- Ejecutar analisis completo.
- Abrir detalle de una accion.

### 15.2 Listas personalizadas

El usuario puede crear listas:

```text
Swing
Tech
Dividendos
Semiconductores
```

Cada ticker puede asignarse a una lista o quedar en `General`.

### 15.3 Detalle de accion

La pantalla de detalle muestra:

- Ultimos analisis.
- Precios recientes.
- Grafica de cierres.
- 52 week high.
- 52 week low.
- P/E trailing o forward si esta disponible.

### 15.4 Perfil

El usuario puede editar:

- Nombre.
- Apellido.
- Email.
- Tamano de cuenta.
- Porcentaje de riesgo por operacion.
- Activar/desactivar email despues de `Analyze All`.

---

## Capitulo 16. Informacion fundamental, analistas y smart money

La aplicacion incluye endpoints AJAX para enriquecer la informacion de un ticker.

### 16.1 Company info

Obtiene desde `yfinance`:

- Nombre de la empresa.
- Sector.
- Industria.
- Pais.
- Website.
- Descripcion.
- Market cap.
- Precio actual.
- Moneda.
- Exchange.
- 52 week high/low.
- Consenso de analistas.
- Target mean, high y low.
- Numero de analistas.

### 16.2 Smart money

Obtiene:

- Institutional holders.
- Insider transactions.

Para institucionales muestra:

- Holder.
- Shares.
- Porcentaje retenido.
- Valor.
- Cambio.
- Fecha.
- Direccion: buy, sell o neutral.

Para insiders muestra:

- Insider.
- Cargo.
- Fecha.
- Direccion.
- Acciones.
- Valor.
- Texto resumido.

Ejemplo:

```text
Holder: Vanguard
Shares: 120.5M
Value: $18.20B
Change: +1.25%
Direction: buy
```

---

## Capitulo 17. Correos y reportes

Despues de `Analyze All`, si el usuario tiene email y el perfil permite reportes, el sistema envia un correo con:

- Senales `BUY`.
- Planes validos.
- Entrada.
- Stop.
- Target.
- Acciones.
- Riesgo.
- Recompensa.
- R:R.
- Accion sugerida: `BUY`, `WAIT` o `REJECT`.

Tambien existe un comando con opcion `--notify` que envia alertas de BUY por ticker.

Importante: el envio depende de variables de entorno:

```text
EMAIL_HOST_USER
EMAIL_HOST_PASSWORD
```

---

## Capitulo 18. Comandos de administracion

### 18.1 Ejecutar servidor

```bash
python manage.py runserver
```

### 18.2 Aplicar migraciones

```bash
python manage.py migrate
```

### 18.3 Crear superusuario

```bash
python manage.py createsuperuser
```

### 18.4 Analizar tickers existentes

```bash
python manage.py analyze_stocks
```

Analizar solo un ticker:

```bash
python manage.py analyze_stocks --symbol AAPL
```

Enviar notificaciones de BUY:

```bash
python manage.py analyze_stocks --notify
```

### 18.5 Analisis diario completo

```bash
python manage.py daily_analysis
```

Forzar reanalisis aunque ya exista analisis de hoy:

```bash
python manage.py daily_analysis --force
```

Este comando:

1. Analiza tickers en watchlists.
2. Actualiza `TickerAnalysis`.
3. Genera planes de trading por usuario.

### 18.6 Actualizar paper trades

```bash
python manage.py update_trades
```

Este comando:

1. Activa operaciones pendientes si el precio toca la entrada.
2. Cierra operaciones activas si tocan stop, target o limite de 15 dias.
3. Muestra resumen de pendientes, activas, cerradas y win rate.

---

## Capitulo 19. Ejemplos practicos

### 19.1 Ejemplo completo de operacion valida

Datos:

```text
Ticker: XYZ
Close: 100
SMA9: 101
SMA20: 99
SMA50: 108
SMA200: 92
RSI: 52
Volumen: 1,300,000
Avg Vol 20: 1,000,000
SPY: sobre SMA200
Estrategias: Sweet Spot, RSI Rebound
```

Niveles:

```text
Entrada: 100.50
Stop: 94.00
Target: 114.48
Riesgo: 6.50
Reward: 13.98
R:R: 2.15
```

Validacion:

```text
Estructura: alcista
RVOL: 1.30 strong
Soporte: 95
Resistencia: 116
Stop: bajo soporte y no demasiado lejos
Target: bajo resistencia
R:R: mayor a 1.7 para pullback

Estado: VALID
Accion: BUY
```

### 19.2 Ejemplo de watchlist

Datos:

```text
Ticker: ABC
Estrategia: Sweet Spot
SPY: sobre SMA200
Estructura: debil
RVOL: 0.92
Soporte: 48
Stop: 47
R:R: 1.8
```

Fallas:

```text
Estructura debil
Volumen bajo
```

Resultado:

```text
2 fallas -> WATCHLIST
Accion: WAIT
```

Interpretacion: la idea no se rechaza totalmente, pero se espera mejor confirmacion.

### 19.3 Ejemplo invalido por SPY

Datos:

```text
Ticker: DEF
Setup tecnico: bueno
SPY close: 410
SPY SMA200: 430
```

Resultado:

```text
SPY debajo de SMA200
Estado: INVALID
Accion: REJECT
```

Interpretacion: aunque la accion parezca atractiva, el mercado general esta debil.

### 19.4 Ejemplo invalido por breakout sin volumen

Datos:

```text
Ticker: GHI
Estrategia: Fresh SMA50 Breakout
RVOL: 0.85
R:R: 2.4
SPY: OK
```

Resultado:

```text
Breakout requiere RVOL >= 1.2
Falla por volumen
Estado probable: WATCHLIST o INVALID segun otras fallas
```

Interpretacion: una ruptura sin volumen puede ser falsa.

### 19.5 Ejemplo de target ajustado

Datos:

```text
Entrada: 100
Stop: 94
Target original: 120
Resistencia cercana: 112
```

Ajuste:

```text
Target ajustado = 112 * 0.993 = 111.22
Nuevo R:R = (111.22 - 100) / (100 - 94) = 1.87
```

Si la estrategia es pullback:

```text
Minimo requerido: 1.7
Resultado: R:R aceptable
```

Si fuera breakout:

```text
Minimo requerido: 2.0
Resultado: R:R insuficiente
```

---

## Capitulo 20. Reglas importantes y limitaciones

### 20.1 No es asesoramiento financiero

El sistema es una herramienta de analisis. No garantiza resultados ni debe interpretarse como recomendacion financiera.

### 20.2 Datos externos pueden fallar

El proyecto depende de `yfinance`. Si Yahoo Finance cambia datos, limita peticiones o devuelve campos vacios, algunas funciones pueden fallar o mostrar datos incompletos.

### 20.3 El paper trading usa datos diarios

El sistema no conoce el orden intradia exacto. Si en el mismo dia se toca stop y target, asume stop primero para ser conservador.

### 20.4 El SPY es filtro de mercado

El filtro SPY puede bloquear buenas acciones individuales cuando el mercado general esta debil. Esta decision es intencional: el proyecto prioriza preservacion de capital.

### 20.5 El score no es una probabilidad

Un score de 80 no significa 80% de probabilidad de ganar. Significa que el setup cumple muchos criterios de calidad definidos por el sistema.

### 20.6 Una senal BUY no siempre produce un trade VALID

El analisis tecnico puede marcar `BUY`, pero el validador puede rechazar el plan si:

- No hay soporte claro.
- Hay resistencia demasiado cerca.
- El R:R cae por debajo del minimo.
- El volumen es debil.
- SPY esta bajista.
- La estructura no confirma.

---

## Capitulo 21. Glosario

| Termino | Significado |
|---|---|
| OHLCV | Open, High, Low, Close, Volume |
| SMA | Simple Moving Average |
| SMA9 | Media movil simple de 9 dias |
| SMA20 | Media movil simple de 20 dias |
| SMA50 | Media movil simple de 50 dias |
| SMA200 | Media movil simple de 200 dias |
| RSI | Relative Strength Index |
| RVOL | Volumen relativo: volumen actual / promedio 20 dias |
| Pullback | Retroceso dentro de una tendencia |
| Breakout | Ruptura de una resistencia o media |
| Stop loss | Precio donde se limita la perdida |
| Target | Precio objetivo |
| R:R | Ratio riesgo/beneficio |
| Pivot high | Maximo local confirmado |
| Pivot low | Minimo local confirmado |
| Higher High | Maximo mayor al anterior |
| Higher Low | Minimo mayor al anterior |
| SPY | ETF usado como proxy del S&P 500 |
| Watchlist | Lista de acciones vigiladas |
| Paper trading | Simulacion sin dinero real |
| PnL | Profit and Loss |
| Profit factor | Ganancia total / perdida total |
| Expectancy | Ganancia promedio esperada por trade |

---

## Resumen ejecutivo

StockAnalyzer es un sistema completo para convertir analisis tecnico en un proceso disciplinado. Primero detecta setups, luego calcula niveles de trading, despues valida la calidad del plan y finalmente mide el resultado en paper trading.

La parte mas importante del proyecto es el validador. Su logica evita depender solo de una senal atractiva y obliga a revisar mercado, estructura, volumen, soporte, resistencia y R:R. Esa separacion entre "detectar una oportunidad" y "validar si merece operarse" es el nucleo del software.
