# MOEX Trade Bot — Sandbox-only

Автоматизация: GitHub Actions + T-Invest Sandbox + Gmail + ChatGPT Work.

## Безопасность

Репозиторий жёстко работает только с T-Invest Sandbox:

- production endpoint в коде отсутствует;
- реальные деньги не используются;
- автоматический вход разрешён только для LONG BUY по `share` и `etf`; standalone SELL от Work заблокирован, выходами управляет protective lifecycle;
- для всех брокерских заявок `confirmMarginTrade=false`;
- BUY требует protocol v2, корректный HMAC, `execution_capability=true`, hard-risk checks и живой маркер `[TRADE-SANDBOX-READY]`;
- readiness-маркер создаётся только после фактического Sandbox smoke-теста entry → STOP/TAKE → TIME_STOP/FORCE_EXIT → нулевая позиция;
- без readiness-маркера scanner продолжает отправлять только analysis-only сигналы.

## Текущая цепочка

```
GitHub scanner
  -> Gmail alias (gera5malyov+trade@gmail.com)
  -> ChatGPT Work reviewer
  -> Gmail [TRADE-CMD]
  -> GitHub command processor
  -> T-Invest Sandbox
  -> Gmail [TRADE-EXEC]

GitHub command processor
  -> lifecycle reconciliation
  -> STOP / TAKE / sibling cancel / TIME_STOP / FORCE_EXIT
```

Work reviewer читает актуальные:
- `prompts/trading_risk_manager_ru.md`
- `prompts/chatgpt_work_trade_reviewer_ru.md`

## Расписание

- risk baseline: по будням в 06:00 МСК;
- scanner: по будням каждые 15 минут, 10:00–17:45 МСК; исполняемое окно жёстко 10:05–17:45 МСК;
- command processor + lifecycle monitor: каждые 5 минут, 09:45–18:45 МСК;
- raw daily snapshot завершившегося дня: 00:05 МСК следующего календарного дня;
- ChatGPT daily report: 23:59 МСК.

Отдельный lifecycle workflow оставлен manual-only как аварийный/диагностический запуск.

## Scanner

Scanner:
- анализирует акции, ETF/фонды, облигации, валюту, фьючерсы, опционы и DFA;
- для биржевых классов отбирает только `REAL_EXCHANGE_MOEX`;
- DFA остаётся отдельным analysis-only специальным классом;
- использует bulk last/close prices;
- выполняет детерминированный strategy pre-check для SHARE/ETF: 1D режим → 1h рабочий тренд → 15m setup → 5m confirmation → 1m timing;
- поддерживает LONG setup types: TREND_CONTINUATION / BREAKOUT / MEAN_REVERSION;
- quality_score pre-check используется только для ранжирования и не является вероятностью прибыли;
- pre-check отсеивает широкий spread и неблагоприятный технический режим до независимого reviewer;
- обогащает кандидата стаканом, торговым статусом, 1m/5m/15m/1h/1D свечами, EMA9/21, RSI14, ATR14, VWAP, relative volume, realized volatility и оценкой оборота;
- передаёт доступный RUB cash и состояние портфеля;
- передаёт independently computed daily/weekly/monthly P&L, high-water drawdown, consecutive losses и статус торгового окна из подписанного baseline v3;
- имеет cooldown по instrument UID, чтобы не слать один и тот же инструмент на каждом запуске.

`execution_capability=true` scanner может выставить только для share/ETF и только если:
1. существует криптографически валидный `[TRADE-SANDBOX-READY]` для текущего Sandbox-счёта;
2. доступен подписанный baseline v3;
3. рассчитаны daily/weekly/monthly P&L и high-water context;
4. consecutive losses рассчитаны и loss-cooldown не активен;
5. сейчас разрешённое торговое окно;
6. инструмент доступен через API, ликвиден и находится в NORMAL_TRADING;
7. полный 1m–1D technical snapshot доступен.

Во всех остальных случаях сигнал analysis-only.

## Hard risk

Кодовый hard-risk выполняется независимо от решения ChatGPT Work:

- риск обычной сделки ≤ 0,25% капитала;
- стоимость позиции ≤ 10% капитала;
- максимум 2 открытые позиции;
- дневной stop = 0,75% капитала;
- недельный stop = 2% капитала начала недели;
- месячный stop = 4% капитала начала месяца;
- при просадке более 3% от signed high-water mark risk budget уменьшается вдвое;
- после 2 последовательных убыточных strategy lifecycle — пауза минимум 2 часа;
- после 3 последовательных убытков новые BUY блокируются до следующего торгового дня;
- повторный вход в тот же instrument UID блокируется;
- для акций блокируется новый BUY в уже занятом секторе;
- проверяются собственные деньги/позиция через Sandbox MaxLots;
- перед BUY используется Sandbox OrderPrice;
- любое пополнение/вывод после релевантного daily/weekly/monthly baseline блокирует BUY;
- live order book перед BUY должен быть не старше 60 секунд;
- код независимо проверяет торговое окно 10:05–17:45 МСК;
- автоматический overnight/NEXT_DAY запрещён; TIME_STOP для BUY должен быть в тот же день и не позднее 18:30 МСК.

T-Invest Sandbox не всегда рассчитывает portfolio daily yield, поэтому дневной P&L считается как:

```
current portfolio equity - start-of-session baseline equity
```

Если baseline, equity или операции нельзя подтвердить — BUY fail-closed.

## Protective lifecycle

После фактического исполнения BUY:

1. вход выставляется LIMIT + `TIME_IN_FORCE_FILL_AND_KILL`;
2. защита ставится только на реально исполненные лоты;
3. создаются STOP_LOSS и TAKE_PROFIT через Sandbox stop-order API;
4. при срабатывании одной защиты sibling отменяется;
5. частичное/отклонённое защитное исполнение приводит к принудительному закрытию остатка;
6. TIME_STOP отменяет защитные заявки и закрывает остаток marketable LIMIT по best bid;
7. если установка защиты после входа не удалась, позиция немедленно переводится в FORCE_EXIT;
8. lifecycle state сохраняется append-only в Gmail как `[TRADE-LIFECYCLE] <signal_id>`, поэтому monitor восстанавливается после перезапуска.

Broker-side STOP/TAKE остаются активны между 5-минутными monitor runs. Автоматический overnight пока запрещён, потому что lifecycle monitor не работает непрерывно ночью/вечером.

## Risk baseline v3

По будням в 06:00 МСК создаётся подписанный:

`[TRADE-RISK-BASELINE] YYYY-MM-DD`

Baseline v3 содержит:
- фактический Sandbox portfolio equity;
- week-start equity/timestamp;
- month-start equity/timestamp;
- signed high-water mark.

Hard-risk независимо считает daily/weekly/monthly P&L и fail-closed блокирует BUY при отсутствии или повреждении baseline.

## Sandbox readiness

В коде `PROTECTIVE_ORDER_LIFECYCLE_IMPLEMENTED=True`, но этого недостаточно для BUY.

Дополнительный runtime gate требует Gmail-маркер:

`[TRADE-SANDBOX-READY]`

One-time workflow `.github/workflows/sandbox-lifecycle-smoke.yml` настроен на 21.09.2026 в 10:10, 11:10 и 12:10 МСК, только в основной сессии. Он:

- отказывается запускаться при существующей позиции;
- использует максимум 1 лот SBER на MOEX;
- проверяет hard-risk;
- проверяет фактический entry fill;
- проверяет активные STOP_LOSS и TAKE_PROFIT у брокера;
- инициирует TIME_STOP;
- проверяет нулевой остаток;
- только после этого создаёт readiness-маркер.

Если любой шаг не подтверждён, readiness не создаётся и автоматический BUY остаётся заблокированным.

## Protocol v2

HMAC подписывает неизменяемую идентичность:

- signal_id;
- created_at;
- ticker;
- class_code;
- instrument_uid;
- instrument_type;
- execution_capability.

Reviewer не может незаметно поменять инструмент или повысить `execution_capability=false` до true.

## Комиссии

Sandbox commission и реальная экономика не смешиваются.

Reviewer отдельно оценивает:
- `SANDBOX_COST`;
- `REALISTIC_TRADER_COST`.

Реальные тарифы должны перепроверяться по официальному T-Bank источнику.

## Проверки

Подтверждено unit/CI:
- protocol v2 и HMAC identity;
- signed readiness и signed internal journals;
- hard-risk: 0,25%, 10%, 2 позиции, net R/R >= 2;
- daily/weekly/monthly loss limits;
- high-water drawdown multiplier;
- 2-loss cooldown;
- hard trading window;
- MOEX-only scanner;
- STOP/TAKE payload semantics;
- protective lifecycle, sibling cancellation и verified force-exit;
- realized lifecycle P&L journal;
- compileall для всех `src/` и `scripts/`.

Подтвержденный транспорт сигналов:
- GitHub → Gmail plus-alias;
- Gmail → ChatGPT Work (требует отдельного активного Work-триггера);
- Work → `[TRADE-CMD]`;
- GitHub command processor → signed `[TRADE-EXEC]`.


## Калибровка и benchmark

Reviewer журналирует для каждого решения, включая SKIP:
- MARKET_REGIME;
- PROBABILITY_SUCCESS_PERCENT, если калиброванная P доступна;
- EXPECTED_VALUE_RUB;
- COUNTER_ARGUMENT;
- BENCHMARK_CHECK;
- DATA_COMPLETENESS.

Модель не имеет права придумывать P. Пока статистики недостаточно, Sandbox работает в CALIBRATION_MODE: P/EV могут быть недоступны, а production всё равно запрещён. Benchmark FAIL или DATA_COMPLETENESS=PARTIAL блокирует BUY.

Автоматический counterfactual outcome для каждого NO_TRADE пока не реализован; это отдельный аналитический модуль и не является условием безопасности Sandbox executor.

## Что остаётся перед автоматическими Sandbox BUY

1. В 06:00 МСК 21.09.2026 должен создаться новый signed baseline v3.
2. One-time live Sandbox smoke должен подтвердить реальный entry → активные STOP/TAKE → безопасный TIME_STOP/FORCE_EXIT → нулевую позицию.
3. Только после этого появится signed `[TRADE-SANDBOX-READY]`.

Даже после readiness Work и hard-risk могут и должны вернуть SKIP.

Исполнение облигаций, валюты, металлов, фьючерсов, опционов и DFA остаётся заблокированным до отдельной валидации price semantics и lifecycle для каждого класса.

## Secrets

GitHub Secrets:
- `TINVEST_TOKEN`
- `MAIL_USER`
- `MAIL_APP_PASSWORD`

Никогда не публиковать токены, app passwords или auth_token.

## Production

Production-контур отсутствует. Перед любыми реальными деньгами требуется отдельный проектный этап, ручное решение владельца и минимум 100 проверяемых Sandbox/исторических сигналов. Sandbox readiness не является разрешением production.
