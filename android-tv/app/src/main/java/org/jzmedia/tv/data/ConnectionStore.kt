package org.jzmedia.tv.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class SavedConnection(val address: String, val token: String, val serverId: String = "", val viewingToken: String = "",
                           val viewingGeneration: Long = 0)

/** No credentials in backups: the manifest disables backups and the key never leaves Keystore. */
class ConnectionStore(context: Context) {
    private val prefs = context.getSharedPreferences("tv_connection", Context.MODE_PRIVATE)
    private val alias = "jzmedia.connection.v1"

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }

    fun load(): SavedConnection? {
        val address = prefs.getString("address", null) ?: return null
        val encrypted = prefs.getString("token", "").orEmpty()
        val token = if (encrypted.isBlank()) "" else {
            val iv = Base64.decode(prefs.getString("iv", ""), Base64.NO_WRAP)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
            cipher.doFinal(Base64.decode(encrypted, Base64.NO_WRAP)).toString(Charsets.UTF_8)
        }
        val viewing = prefs.getString("viewing_token", "").orEmpty().let { encryptedViewing ->
            if (encryptedViewing.isBlank()) "" else {
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128,
                    Base64.decode(prefs.getString("viewing_iv", ""), Base64.NO_WRAP)))
                cipher.doFinal(Base64.decode(encryptedViewing, Base64.NO_WRAP)).toString(Charsets.UTF_8)
            }
        }
        return SavedConnection(address, token, prefs.getString("server_id", "").orEmpty(), viewing,
            prefs.getLong("viewing_generation", 0))
    }

    fun save(connection: SavedConnection) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encrypted = cipher.doFinal(connection.token.toByteArray(Charsets.UTF_8))
        val viewingCipher = Cipher.getInstance("AES/GCM/NoPadding")
        viewingCipher.init(Cipher.ENCRYPT_MODE, key())
        val encryptedViewing = viewingCipher.doFinal(connection.viewingToken.toByteArray(Charsets.UTF_8))
        check(prefs.edit().putString("address", connection.address)
            .putString("token", Base64.encodeToString(encrypted, Base64.NO_WRAP))
            .putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString("viewing_token", Base64.encodeToString(encryptedViewing, Base64.NO_WRAP))
            .putString("viewing_iv", Base64.encodeToString(viewingCipher.iv, Base64.NO_WRAP))
            .putLong("viewing_generation", connection.viewingGeneration)
            .putString("server_id", connection.serverId).commit()) { "无法保存连接配置" }
    }

    fun clear() { prefs.edit().clear().apply() }
}
