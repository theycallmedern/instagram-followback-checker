# Instagram Non-Followers Checker

Локальный скрипт, который берет официальный экспорт Instagram и показывает:

- на кого вы подписаны;
- кто из них не подписан на вас в ответ.

Скрипт не использует неофициальный API и не логинится в Instagram.

## Что нужно скачать из Instagram

В Instagram выбери экспорт данных аккаунта в формате `JSON`.

Обычно это делается через:

- `Settings`
- `Accounts Center`
- `Your information and permissions`
- `Download your information`

После этого у тебя будет папка с JSON-файлами или ZIP-архив.

## Запуск

Из этой папки:

```bash
python3 instagram_nonfollowers.py /path/to/instagram-export
```

Если у тебя ZIP:

```bash
python3 instagram_nonfollowers.py /path/to/instagram-export.zip
```

## Сохранить результат в файл

```bash
python3 instagram_nonfollowers.py /path/to/instagram-export \
  --csv not_following_back.csv \
  --json not_following_back.json
```

## Полезно

- `--verbose` покажет, какие JSON-файлы были использованы.
- Имена приводятся к lowercase, потому что usernames в Instagram регистронезависимы.

## Пример

```bash
python3 instagram_nonfollowers.py ~/Downloads/instagram-export --verbose
```
