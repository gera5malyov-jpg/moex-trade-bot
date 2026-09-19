# MOEX Trade Bot — Sandbox-only

Автоматизация: GitHub Actions + T-Invest Sandbox + Outlook bridge + Gmail + ChatGPT Work.

## Безопасность

Репозиторий жёстко работает только с T-Invest Sandbox:

- production endpoint в коде отсутствует;
- реальные деньги не используются;
- исполняемые BUY/SELL пока разрешены только для `share` и `etf`;
- для всех брокерских заявок `confirmMarginTrade=false`;
- BUY требует protocol v2, корректный HMAC, `execution_capability=true`, hard-risk checks и живой маркер `[TRADE-SANDBOX-READY]`;
- readiness-маркер создаётся только после фактического Sandbox smoke-теста entry → STOP/TAKE → TIME_STOP/FORCE_EXIT → нулевая позиция;
- без readiness-маркера scanner продолжает отправлять только analysis-only сигналы.

## Текущая цепочка

```
GitHub scanner
  -> Outlook (anthony-19951936@outlook.com)
  -> automatic Outlook forward
  -> Gmail (gera5malyov@gmail.com)
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
- scanner: по будням каждые 15 минут, 07:00–23:45 МСК;
- command processor + lifecycle monitor: каждые 5 минут, 06:00–23:55 МСК;
- raw daily snapshot: 23:52 МСК;
- ChatGPT daily report: 23:59 МСК.

Отдельный lifecycle workflow оставлен manual-only как аварийный/диагностический запуск.

## Scanner

Scanner:
- анализирует акции, ETF/фонды, облигации, валюту, фьючерсы, опционы и DFA;
- для биржевых классов отбирает только `REAL_EXCHANGE_MOEX`;
- DFA остаётся отдельным analysis-only специальным классом;
- использует bulk last/close prices;
- обогащает кандидата стаканом, торговым статусом, 5m/15m свечами, EMA9/21, RSI14, ATR14, VWAP, relative volume;
- передаёт доступный RUB cash и состояние портфеля;
- передаёт independently computed daily P&L и consecutive losses, когда доступен дневной baseline;
- имеет cooldown по instrument UID, чтобы не слать один и тот же инструмент на каждом запуске.

`execution_capability=true` scanner может выставить только для share/ETF и только если:
1. существует `[TRADE-SANDBOX-READY]`;
2. дневной baseline доступен;
3. daily P&L рассчитан;
4. consecutive losses рассчитаны.

Во всех остальных случаях сигнал analysis-only.

## Hard risk

Кодовый hard-risk выполняется независимо от решения ChatGPT Work:

- риск обычной сделки ≤ 0,25% капитала;
- стоимость позиции ≤ 10% капитала;
- максимум 2 открытые позиции;
- дневной stop = 0,75% капитала;
- после 3 последовательных убыточных SELL новые BUY блокируются;
- повторный вход в тот же instrument UID блокируется;
- для акций блокируется новый BUY в уже занятом секторе;
- проверяются собственные деньги/позиция через Sandbox MaxLots;
- перед BUY используется Sandbox OrderPrice;
- любое пополнение/вывод после дневного baseline блокирует BUY.

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

Broker-side STOP/TAKE остаются активны между 5-минутными monitor runs.

## Daily risk baseline

По будням в 06:00 МСК создаётся:

`[TRADE-RISK-BASELINE] YYYY-MM-DD`

Baseline содержит фактический Sandbox portfolio equity. Hard-risk использует его вместе с операциями брокера.

## Sandbox readiness

В коде `PROTECTIVE_ORDER_LIFECYCLE_IMPLEMENTED=True`, но этого недостаточно для BUY.

Дополнительный runtime gate требует Gmail-маркер:

`[TRADE-SANDBOX-READY]`

One-time workflow `.github/workflows/sandbox-lifecycle-smoke.yml` настроен на 21.09.2026 утром по Москве. Он:

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

Подтверждено:
- protocol v2 tests;
- hard-risk tests;
- baseline daily P&L tests;
- consecutive-loss tests;
- MOEX-only scanner tests;
- protective lifecycle fake-broker tests;
- compileall для всех `src/` и `scripts/`;
- daily Sandbox snapshot → Gmail;
- Work reviewer → `[TRADE-CMD]`;
- command processor → `[TRADE-EXEC]`;
- Outlook вручную пересланный signal → Gmail event → Work;
- live risk baseline → Gmail;
- live lifecycle monitor на пустом состоянии.

## Текущий внешний блокер

Свежий контрольный сигнал `118c4e24-6b85-4a1b-933c-22a5556e6839` успешно дошёл GitHub → Outlook, но автоматическая пересылка Outlook → Gmail в контрольном прогоне не появилась.

До исправления Outlook rule:
- scanner безопасно может работать;
- сигналы будут доходить до Outlook;
- Work не получит их автоматически;
- торговая команда не создастся;
- сделки не выполнятся.

Это fail-closed состояние.

## Что остаётся перед полностью автоматическими Sandbox-сделками

1. Подтвердить/исправить Outlook rule, автоматически пересылающее `[TRADE-SIGNAL]` на Gmail.
2. Дождаться успешного one-time live Sandbox lifecycle smoke 21.09.2026; readiness создаётся автоматически только при полном успехе.
3. После этого share/ETF scanner сможет выдавать signed `execution_capability=true`, а Work и hard-risk сохранят право сделать SKIP.

Исполнение облигаций, валюты, металлов, фьючерсов, опционов и DFA остаётся заблокированным до отдельной валидации price semantics и защитного lifecycle для каждого класса.

## Secrets

GitHub Secrets:
- `TINVEST_TOKEN`
- `MAIL_USER`
- `MAIL_APP_PASSWORD`

Никогда не публиковать токены, app passwords или auth_token.

## Production

Production-контур отсутствует. Любое будущее подключение реальных денег — отдельный проектный этап с отдельной валидацией и явным решением пользователя.
