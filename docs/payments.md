# 支付接口配置

支付模块当前上线微信支付和支付宝两种一次性点数包扫码付款。PayPal 代码已预留，但结账页面固定显示“暂未支持”。

前端内置 `frontend/public/payment/` 下的微信和支付宝收款码。未配置对应商户 API 时，页面会明确标记为“人工核对”；静态个人收款码没有支付回调，不能自动加点。旧的“扫码加群、联系群主开通”流程和群二维码已删除。

## 接口

- `GET /api/payments/catalog`：套餐和已配置的支付方式。
- `POST /api/payments/orders`：创建订单，请求体为 `{"provider":"paypal|wechat|alipay","plan_id":"go|plus|pro"}`。
- `GET /api/payments/orders/{id}`：查询当前用户的订单状态。
- `POST /api/payments/orders/{id}/paypal/capture`：PayPal 买家批准后收款。
- `POST /api/payments/webhooks/{paypal|wechat|alipay}`：支付平台异步通知。

所有创建、查询和 PayPal 收款接口都需要登录；三个 webhook 是公开地址，但会验证支付平台签名，并再次核对订单金额和币种。

## 生产环境

先设置：

```dotenv
MPOS_PUBLIC_URL=https://mpos.upypi.net
```

完整变量清单在 `backend/.env.example`。生产部署必须把密钥放入平台的 Secret/Environment Variables，不要提交 `.env`。

### PayPal

1. 在 PayPal Developer 创建 REST App，配置 `PAYPAL_CLIENT_ID` 和 `PAYPAL_CLIENT_SECRET`。
2. 测试期间使用 `PAYPAL_ENVIRONMENT=sandbox`；上线后改成 `live`。
3. 创建 webhook，地址为 `https://mpos.upypi.net/api/payments/webhooks/paypal`，至少订阅 `PAYMENT.CAPTURE.COMPLETED`，并设置返回的 `PAYPAL_WEBHOOK_ID`。
4. 完成沙箱验收后才把 `PAYPAL_CHECKOUT_ENABLED` 改为 `true`；当前生产环境保持 `false`。

### 微信支付

使用 API v3 Native 支付，配置商户号、AppID、商户证书序列号、商户私钥、32 字节 API v3 密钥，以及微信支付平台公钥。通知地址：

```text
https://mpos.upypi.net/api/payments/webhooks/wechat
```

PEM 密钥可以写成多行，也可以把换行写成 `\n`。平台公钥模式下同时设置对应的 `WECHAT_PAY_PLATFORM_SERIAL_NO`。

### 支付宝

在开放平台应用中启用电脑网站支付并配置 RSA2 密钥。通知地址：

```text
https://mpos.upypi.net/api/payments/webhooks/alipay
```

配置应用私钥、支付宝公钥和应用 ID；建议额外设置 `ALIPAY_SELLER_ID`，让回调同时校验收款商户。

## 上线检查

1. 先用微信和支付宝小额订单分别完成一次全流程；PayPal 上线前单独完成 Sandbox 验收。
2. 确认订单从 `pending` 变为 `paid`，账户点数只增加一次。
3. 在支付平台控制台重发同一通知，确认点数不重复增加。
4. 核对反向代理保留 HTTPS，并确保三个 webhook 可从公网访问。
