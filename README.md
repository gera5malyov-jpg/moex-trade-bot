# MOEX Trade Bot — Sandbox scaffold

Каркас торговой автоматизации на GitHub Actions + T-Invest API + e-mail handoff.

## Текущий режим

**Только T-Invest Sandbox. Реальные сделки отключены конструктивно.**
В коде нет переключателя на production endpoint.

Схема:
1. strategy.py в будущем находит потенциальный вход.
2. GitHub отправляет письмо [TRADE-SIGNAL] с данными сигнала.
3. Внешний reviewer проверяет сигнал.
4. Reviewer отправляет письмо [TRADE-CMD] в строгом JSON-формате.
5. GitHub читает команду из почты.
6. Команда проходит проверки UUID, HMAC, срока действия, типа ордера и режима Sandbox.
7. Только после этого вызывается PostSandboxOrder.

## T-Invest Sandbox

Для первого запуска нужен только Secret `TINVEST_TOKEN`.

Workflow `Bootstrap T-Invest Sandbox`:
- ищет открытый sandbox-счёт с именем `github-moex-trade-bot`;
- если его нет — создаёт;
- новый счёт автоматически пополняет на **1 000 000 RUB виртуальных денег**;
- если счёт уже существует — повторно деньги не добавляет;
- остальные workflow сами находят этот счёт по имени.

**TINVEST_SANDBOX_ACCOUNT_ID больше не нужен.**

## Важно про ChatGPT без API

GitHub-часть и почтовый протокол не требуют OpenAI API.
Для полностью автоматического шага «пришло письмо → ChatGPT немедленно анализирует → отправляет ответ»
нужен внешний триггер, который умеет запускать модель. Текущий каркас готовит интерфейс для такого
reviewer, но не имитирует его.

## GitHub Secrets

- TINVEST_TOKEN
- TRADE_HMAC_SECRET — случайная строка не короче 32 символов
- MAIL_USER
- MAIL_APP_PASSWORD
- MAIL_TO
- COMMAND_ALLOWED_FROM

Для Gmail:
- IMAP: imap.gmail.com:993
- SMTP: smtp.gmail.com:465

## Repository variables

- TRADING_ENABLED=false
- COMMAND_MAX_AGE_MINUTES=15
- MAIL_IMAP_HOST=imap.gmail.com
- MAIL_SMTP_HOST=smtp.gmail.com

## Первый запуск

1. Добавьте `TINVEST_TOKEN` как GitHub Secret.
2. Откройте Actions → `Bootstrap T-Invest Sandbox` → `Run workflow`.
3. Откройте завершившийся запуск → job `bootstrap` → step `Create or verify sandbox account`.
4. В логе увидите:
   - имя sandbox-счёта;
   - его account ID;
   - пополнение на 1 000 000 RUB при первом создании;
   - текущий доступный RUB-баланс при повторном запуске.
5. Ничего из account ID вручную сохранять не надо.

## Стратегия

Стратегия намеренно пока не реализована. Частоту сканирования, universe инструментов,
правила входа/выхода и риск-менеджмент определим отдельно.
