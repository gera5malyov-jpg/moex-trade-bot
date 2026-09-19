# MOEX Trade Bot — Sandbox scaffold

Каркас торговой автоматизации на GitHub Actions + T-Invest API + e-mail handoff.

## Статус

**T-Invest Sandbox подключён и проверен.**
- токен T-Invest работает;
- sandbox-счёт `github-moex-trade-bot` создан;
- на него зачислено **1 000 000 RUB виртуальных денег**;
- TLS настроен через официальный Russian Trusted Root CA, используемый T-Invest SDK;
- реальные сделки отключены конструктивно;
- `TRADING_ENABLED=false`;
- стратегия пока не реализована.

## Схема

1. `strategy.py` в будущем находит потенциальный вход.
2. GitHub отправляет письмо `[TRADE-SIGNAL]` с данными сигнала.
3. Reviewer проверяет сигнал.
4. Reviewer отправляет `[TRADE-CMD]` в строгом JSON-формате.
5. GitHub читает команду из почты.
6. Команда проходит проверки UUID, HMAC, срока действия, типа ордера и режима Sandbox.
7. Только после этого вызывается `PostSandboxOrder`.

## T-Invest Sandbox

Для T-Invest нужен только GitHub Secret:

- `TINVEST_TOKEN`

Sandbox account ID сохранять не нужно. Workflow сам находит счёт по имени
`github-moex-trade-bot`.

Командный HMAC-ключ отдельным секретом больше не хранится: он выводится из
`TINVEST_TOKEN` через SHA-256 с domain separation. Сам T-Invest token в сигнал,
логи или репозиторий не попадает.

## Почта

Для Gmail нужны только два GitHub Secrets:

- `MAIL_USER` — адрес Gmail;
- `MAIL_APP_PASSWORD` — пароль приложения Google.

По умолчанию:
- сигнал отправляется самому себе (`MAIL_TO=MAIL_USER`);
- принимать команды разрешено только от того же адреса
  (`COMMAND_ALLOWED_FROM=MAIL_USER`);
- IMAP: `imap.gmail.com:993`;
- SMTP: `smtp.gmail.com:465`.

При необходимости `MAIL_TO` и `COMMAND_ALLOWED_FROM` можно переопределить
переменными окружения.

## Repository variables

Необязательны, поскольку есть безопасные значения по умолчанию:

- `TRADING_ENABLED=false`
- `COMMAND_MAX_AGE_MINUTES=15`
- `MAIL_IMAP_HOST=imap.gmail.com`
- `MAIL_SMTP_HOST=smtp.gmail.com`

## Важно про автоматический ChatGPT reviewer

GitHub-часть и почтовый протокол не требуют OpenAI API. Но полностью автоматический
шаг «входящее письмо → немедленно запустить ChatGPT → проверить рынок → вернуть
команду» требует входящего триггера модели. В текущем подключении такого Gmail-триггера
нет, поэтому код не изображает несуществующую автоматизацию.

## Стратегия

Стратегия намеренно пока не реализована. Частоту сканирования, universe инструментов,
правила входа/выхода, новости, риск-менеджмент, стопы и тейки определим отдельно.
