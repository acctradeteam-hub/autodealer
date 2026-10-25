package com.lotanalyzer.voice;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.os.Bundle;
import android.speech.RecognizerIntent;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import org.json.JSONObject;

import java.util.ArrayList;

/**
 * «Аукцион: заметки» — список машин на осмотр из программы Lot Analyzer и заметки к ним.
 * Экран — assets/index.html в WebView; кнопка 🎤 вызывает распознавание речи Android (русский),
 * распознанный текст возвращается в страницу. Список и заметки — через ваш закрытый репозиторий GitHub.
 */
public class MainActivity extends Activity {
    private static final int VOICE = 1;
    private WebView web;
    private String voiceVin = "";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);           // заметки и список хранятся в телефоне (localStorage)
        s.setAllowFileAccess(true);
        web.setWebViewClient(new WebViewClient());
        web.addJavascriptInterface(new Bridge(), "Native");
        setContentView(web);
        if (state != null) {
            web.restoreState(state);
        } else {
            web.loadUrl("file:///android_asset/index.html");
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    private class Bridge {
        @JavascriptInterface
        public void voice(String vin) {
            runOnUiThread(() -> {
                voiceVin = vin;
                Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
                i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
                i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ru-RU");
                i.putExtra(RecognizerIntent.EXTRA_PROMPT, "Говорите заметку к машине");
                try {
                    startActivityForResult(i, VOICE);
                } catch (ActivityNotFoundException e) {
                    send("onVoiceError", "На телефоне нет распознавания речи: установите приложение Google и включите голосовой ввод.");
                }
            });
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != VOICE) {
            return;
        }
        if (result == RESULT_OK && data != null) {
            ArrayList<String> said = data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
            if (said != null && !said.isEmpty()) {
                send("onVoice", voiceVin, said.get(0));
            }
        }
    }

    /** Вызов функции страницы со строковыми аргументами (экранированы как JSON). */
    private void send(String fn, String... args) {
        StringBuilder js = new StringBuilder(fn).append("(");
        for (int k = 0; k < args.length; k++) {
            if (k > 0) {
                js.append(",");
            }
            js.append(JSONObject.quote(args[k]));
        }
        js.append(")");
        web.post(() -> web.evaluateJavascript(js.toString(), null));
    }
}
