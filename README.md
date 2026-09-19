# MOEX Trade Bot — Sandbox-only scaffold

Торговая автоматизация на GitHub Actions + T-Invest API + Gmail + ChatGPT Work.

## Текущий статус

**T-Invest Sandbox подключён и проверен.**

- токен T-Invest работает;
- sandbox-счёт `github-moex-trade-bot` создан;
- на счёте 1 000 000 RUB виртуальных денег;
- TLS проверяется через Russian Trusted Root CA;
- production endpoint в коде отсутствует;
- `TRADING_ENABLED=false` по умолчанию;
- BUY дополнительно жёстко заблокирован в коде до реализации защитного lifecycle;
- стратегия сканирования рынка пока НЕ реализована;
- автоматический поиск кандидатов пока НЕ запускается;
- обработчик `[TRADE-CMD]` пока запускается вручную через workflow_dispatch.

Поэтому репозиторий пока является безопасным Sandbox-каркасом, а не готовым автономным торговым роботом.

## Протокол v2

Сигнал и команда используют protocol_version=2.

HMAC теперь подписывает неизменяемую идентичность сигнала:
- signal_id;
- created_at;
- ticker;
- class_code;
- instrument_uid;
- instrument_type;
- execution_capability.

Это не позволяет reviewer незаметно заменить инструмент или включить исполнение там, где GitHub передал `execution_capability=false`.

Основной идентификатор инструмента для исполнения — `instrument_uid`.

## Защита исполнения

Перед заявкой Sandbox-клиент:
1. разрешает инструмент только точным совпадением UID/ticker/class_code;
2. проверяет `GetSandboxMaxLots` по собственным деньгам или собственной позиции;
3. вызывает `GetSandboxOrderPrice` для предварительной стоимости/комиссии;
4. использует LIMIT;
5. использует `TIME_IN_FORCE_FILL_AND_KILL`, чтобы устаревшая цена не оставалась до конца торгового дня;
6. передаёт `confirmMarginTrade=false`;
7. использует один детерминированный orderId на один signal_id.

BUY всё равно заблокирован до реализации:
- подтверждения фактического исполнения входа;
- STOP_LOSS;
- TAKE_PROFIT;
- взаимной отмены защитных заявок;
- TIME_STOP / FORCE_EXIT.

## Комиссии

Нельзя смешивать комиссию Sandbox и реальную экономику тарифа.

Sandbox:
- 0,05% от объёма сделки независимо от инструмента.

Для оценки реальной стратегии используется тариф Т-Инвестиций «Трейдер» и актуальные условия конкретного класса актива. Стратегия хранит два расчёта:
- SANDBOX_COST;
- REALISTIC_TRADER_COST.

## ChatGPT Work reviewer

OpenAI поддерживает event-triggered Work tasks для Gmail у подходящих тарифов.

Готовый проверенный промт:
`prompts/chatgpt_work_trade_reviewer_ru.md`

Ожидаемая схема:
1. GitHub создаёт `[TRADE-SIGNAL]`.
2. Gmail event запускает ChatGPT Work.
3. Work анализирует рынок/новости/риск.
4. Work отправляет новое письмо `[TRADE-CMD] <signal_id>`.
5. GitHub валидирует protocol v2 и только после этого может передать команду в Sandbox.

**Важно:** шаг 5 пока не событийный. Workflow `process-trade-commands.yml` всё ещё manual-only. До добавления безопасного polling/webhook команда сама по себе не разбудит GitHub.

## Стратегия

Правила:
`prompts/trading_risk_manager_ru.md`

Universe для анализа включает:
- акции;
- фонды;
- облигации;
- валюту;
- металлы;
- фьючерсы;
- опционы;
- ЦФА;
- криптосвязанные инструменты.

Это не означает, что все эти классы уже разрешены к исполнению. Исполнение определяется подписанным `execution_capability`.

## Что ещё не готово

Перед автоматическим Sandbox BUY необходимо:
1. реализовать market scanner и enriched `[TRADE-SIGNAL]`;
2. реализовать кодовые hard-risk checks 0,25% / 0,75% / 10% / max 2 positions;
3. реализовать protective-order lifecycle;
4. реализовать persistent journal/deduplication;
5. настроить автоматический запуск command processor;
6. провести end-to-end тест BUY → protective orders → EXIT только в Sandbox;
7. только после этого обсуждать отдельный production-контур.

## Secrets

GitHub Secrets:
- `TINVEST_TOKEN`
- `MAIL_USER`
- `MAIL_APP_PASSWORD`

Не публиковать токены, пароли приложений и auth payload в логах или issue.

## Repository variables

Безопасные значения по умолчанию:
- `TRADING_ENABLED=false`
- `COMMAND_MAX_AGE_MINUTES=15`
- `SIGNAL_MAX_AGE_MINUTES=15`
- `MAIL_IMAP_HOST=imap.gmail.com`
- `MAIL_SMTP_HOST=smtp.gmail.com`

## Важное ограничение

Этот проект не гарантирует прибыль. ChatGPT reviewer — аналитический слой, а не замена кодовым лимитам риска. Любая будущая торговля реальными деньгами должна быть отдельным этапом с независимой валидацией и явным включением production-контура.
