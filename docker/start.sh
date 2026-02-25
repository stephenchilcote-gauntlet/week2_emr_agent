#!/usr/bin/env bash
# Install Let's Encrypt certs into Apache SSL paths before openemr.sh regenerates self-signed ones
LE_CERT="/etc/letsencrypt/live/emragent.404.mn/fullchain.pem"
LE_KEY="/etc/letsencrypt/live/emragent.404.mn/privkey.pem"

if [[ -f "$LE_CERT" && -f "$LE_KEY" ]]; then
    echo "Installing Let's Encrypt certificate for emragent.404.mn"
    cp "$LE_CERT" /etc/ssl/certs/webserver.cert.pem
    cp "$LE_KEY" /etc/ssl/private/webserver.key.pem
fi

# Redirect bare-IP access to the domain name so the cert matches
CONF="/etc/apache2/conf.d/openemr.conf"
if ! grep -q 'emragent.404.mn' "$CONF" 2>/dev/null; then
    echo "Adding IP-to-domain redirect rules"
    sed -i '/<VirtualHost \*:80>/a\
    RewriteEngine On\
    RewriteCond %{HTTP_HOST} !^emragent\\.404\\.mn$ [NC]\
    RewriteRule ^(.*)$ https://emragent.404.mn$1 [R=301,L]' "$CONF"

    sed -i '/<VirtualHost _default_:443>/a\
    RewriteEngine On\
    RewriteCond %{HTTP_HOST} !^emragent\\.404\\.mn$ [NC]\
    RewriteRule ^(.*)$ https://emragent.404.mn$1 [R=301,L]' "$CONF"
fi

exec ./openemr.sh
