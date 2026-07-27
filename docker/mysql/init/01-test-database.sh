#!/bin/bash
set -e

# O entrypoint oficial do MySQL executa este arquivo somente ao criar o volume.
# A permissão é restrita ao banco temporário usado pelo test runner do Django.
mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL
    GRANT ALL PRIVILEGES ON \`test_${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'%';
    FLUSH PRIVILEGES;
EOSQL
