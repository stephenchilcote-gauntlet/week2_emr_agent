<?php

/**
 * HMAC authentication token helper for the Clinical Assistant sidebar.
 *
 * Generates and validates short-lived tokens so that proxy.php can verify
 * the user even when OpenEMR's restoreSession() has clobbered the PHP
 * session cookie (common with multiple browser tabs).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    OpenEMR Community
 * @copyright Copyright (c) 2026 OpenEMR
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\ClinicalAssistant;

class HmacAuth
{
    private const KEY_FILENAME = 'clinical_assistant_hmac.key';
    private const TOKEN_TTL = 7200; // 2 hours — matches OpenEMR session timeout

    /**
     * Return the HMAC signing key, generating it on first use.
     *
     * The key is stored in the site's documents directory alongside
     * OpenEMR's own certificate files.  We use a dedicated key rather
     * than re-purposing the OAuth RSA private key (oaprivate.key).
     */
    public static function getKey(): string
    {
        $dir = $GLOBALS['OE_SITE_DIR'] . '/documents/certificates';
        $path = $dir . '/' . self::KEY_FILENAME;

        if (file_exists($path)) {
            return file_get_contents($path);
        }

        $key = random_bytes(32);
        file_put_contents($path, $key, LOCK_EX);
        return $key;
    }

    /**
     * Create a base64-encoded HMAC token for a given user ID.
     */
    public static function createToken(string $userId): string
    {
        $key = self::getKey();
        $expires = time() + self::TOKEN_TTL;
        $payload = $userId . ':' . $expires;
        $signature = hash_hmac('sha256', $payload, $key);
        return base64_encode($payload . ':' . $signature);
    }

    /**
     * Validate a token and return the user ID, or empty string on failure.
     */
    public static function validateToken(string $token): string
    {
        $decoded = base64_decode($token, true);
        if ($decoded === false) {
            return '';
        }

        $parts = explode(':', $decoded, 3);
        if (count($parts) !== 3) {
            return '';
        }

        [$tokenUser, $tokenExpires, $tokenSig] = $parts;

        if (time() >= (int)$tokenExpires) {
            return '';
        }

        $key = self::getKey();
        $expected = hash_hmac('sha256', $tokenUser . ':' . $tokenExpires, $key);
        if (!hash_equals($expected, $tokenSig)) {
            return '';
        }

        return $tokenUser;
    }
}
