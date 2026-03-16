"""
TPS v2 Strategy Engine (v2.1)
Carver-style continuous forecast system with volatility-targeted sizing.

v2.1 Changes:
- Added rebalance_frequency support (daily / weekly / monthly)
- Fixed CAGR calculation to use actual calendar days
- Fixed Sharpe annualization to account for BTC's 7-day trading week
"""

import numpy as np
import pandas as pd
from .indicators import (
    combined_forecast, regime_filter, instrument_volatility, sma
)


class StrategyV2:
    def __init__(self, config: dict):
        self.config = config
        self.ewmac_cfg = config['ewmac']
        self.sizing_cfg = config['sizing']
        self.regime_cfg = config['regime']
        self.forecast_cfg = config['forecast']
        self.buffer_cfg = config['buffering']
        self.friction_cfg = config.get('frictions', {})
    
    def compute_forecasts(self, price: pd.Series, symbol: str) -> pd.DataFrame:
        regime = regime_filter(price, self.regime_cfg['sma_period'])
        forecast = combined_forecast(
            price=price,
            variations=self.ewmac_cfg['variations'],
            forecast_scalars=self.ewmac_cfg['forecast_scalars'],
            forecast_weights=self.ewmac_cfg['forecast_weights'],
            forecast_div_multiplier=self.ewmac_cfg['forecast_div_multiplier'],
            forecast_cap=self.forecast_cfg['cap'],
            forecast_floor=self.forecast_cfg['floor'],
            vol_lookback=self.sizing_cfg['vol_lookback_days']
        )
        if self.regime_cfg['enabled']:
            forecast = forecast * regime
        inst_vol = instrument_volatility(price, self.sizing_cfg['vol_lookback_days'])
        return pd.DataFrame({
            'forecast': forecast, 'regime': regime, 'inst_vol': inst_vol,
            'price': price, 'sma_200': sma(price, self.regime_cfg['sma_period'])
        }, index=price.index)
    
    def compute_target_position(self, forecast, inst_vol, price, capital, instrument_weight):
        if inst_vol <= 0 or price <= 0 or np.isnan(inst_vol) or np.isnan(forecast):
            return 0.0
        vol_target = self.sizing_cfg['vol_target_pct']
        idm = self.sizing_cfg['instrument_div_multiplier']
        return (capital * vol_target * instrument_weight * idm * (forecast / 10.0)) / (inst_vol * price)
    
    def apply_buffer(self, target_position, current_position, avg_position):
        if avg_position <= 0:
            return target_position
        threshold = self.buffer_cfg['threshold_fraction'] * avg_position
        if abs(target_position - current_position) < threshold:
            return current_position
        return target_position


def _is_rebalance_day(date, prev_date, frequency):
    if frequency == 'daily':
        return True
    elif frequency == 'weekly':
        if prev_date is None:
            return True
        return date.isocalendar()[1] != prev_date.isocalendar()[1]
    elif frequency == 'monthly':
        if prev_date is None:
            return True
        return date.month != prev_date.month
    return True


class Backtester:
    def __init__(self, strategy, data, config):
        self.strategy = strategy
        self.data = data
        self.config = config
        self.initial_capital = config['portfolio']['initial_capital']
        self.rebalance_freq = config.get('rebalance', {}).get('frequency', 'daily')
        self.forecasts = {}
        for symbol, df in data.items():
            print(f"  Computing forecasts for {symbol}...")
            self.forecasts[symbol] = strategy.compute_forecasts(df['Close'], symbol)
    
    def run(self):
        symbols = list(self.data.keys())
        instrument_weights = self.config['sizing']['instrument_weights']
        friction_pct = self.config['frictions']['commission_pct'] + self.config['frictions']['slippage_pct']
        
        date_ranges = [self.data[s].index for s in symbols]
        common_start = max(idx.min() for idx in date_ranges)
        common_end = min(idx.max() for idx in date_ranges)
        all_dates = sorted(d for d in set().union(*[set(idx) for idx in date_ranges])
                           if common_start <= d <= common_end)
        
        warmup = 250
        if len(all_dates) <= warmup:
            raise ValueError("Not enough data for warmup period")
        
        capital = self.initial_capital
        positions = {s: 0.0 for s in symbols}
        avg_positions = {s: 0.0 for s in symbols}
        equity_list, position_records, trade_list, daily_pnl_records = [], [], [], []
        prev_date = None
        
        for i, date in enumerate(all_dates):
            if i < warmup:
                equity_list.append({'date': date, 'equity': capital})
                prev_date = date
                continue
            
            # --- P&L from existing positions ---
            day_pnl = {}
            for symbol in symbols:
                fc_data, price_data = self.forecasts[symbol], self.data[symbol]
                if date not in fc_data.index or date not in price_data.index:
                    day_pnl[symbol] = 0.0
                    continue
                close_price = price_data.loc[date, 'Close']
                if i > warmup and positions[symbol] != 0:
                    p_date = all_dates[i - 1]
                    if p_date in price_data.index:
                        pnl = positions[symbol] * (close_price - price_data.loc[p_date, 'Close'])
                        day_pnl[symbol] = pnl
                        capital += pnl
                    else:
                        day_pnl[symbol] = 0.0
                else:
                    day_pnl[symbol] = 0.0
            
            # --- Rebalance check ---
            if _is_rebalance_day(date, prev_date, self.rebalance_freq):
                target_positions = {}
                for symbol in symbols:
                    fc_data = self.forecasts[symbol]
                    if date not in fc_data.index:
                        target_positions[symbol] = 0.0
                        continue
                    row = fc_data.loc[date]
                    forecast, inst_vol, price = row['forecast'], row['inst_vol'], row['price']
                    if np.isnan(forecast) or np.isnan(inst_vol):
                        target_positions[symbol] = 0.0
                        continue
                    weight = instrument_weights.get(symbol, 1.0 / len(symbols))
                    target = self.strategy.compute_target_position(forecast, inst_vol, price, capital, weight)
                    target = round(target)
                    target = self.strategy.apply_buffer(target, positions[symbol],
                                                        max(avg_positions.get(symbol, abs(target)), 1))
                    target_positions[symbol] = round(target)
                
                # Execute trades
                for symbol in symbols:
                    target = target_positions[symbol]
                    trade_shares = target - positions[symbol]
                    if trade_shares != 0:
                        price_data = self.data[symbol]
                        if date not in price_data.index:
                            continue
                        fill_price = price_data.loc[date, 'Close']
                        friction_cost = abs(trade_shares) * fill_price * friction_pct
                        capital -= friction_cost
                        trade_list.append({
                            'date': date, 'symbol': symbol, 'shares': trade_shares,
                            'price': fill_price, 'value': trade_shares * fill_price,
                            'friction': friction_cost,
                            'position_before': positions[symbol], 'position_after': target,
                            'forecast': self.forecasts[symbol].loc[date, 'forecast'] if date in self.forecasts[symbol].index else 0
                        })
                        old_pos = positions[symbol]
                        positions[symbol] = target
                        if avg_positions[symbol] == 0:
                            avg_positions[symbol] = abs(target)
                        else:
                            avg_positions[symbol] = 0.95 * avg_positions[symbol] + 0.05 * abs(target)
            
            # --- Record state ---
            pos_record = {'date': date}
            pnl_record = {'date': date}
            for symbol in symbols:
                pos_record[symbol] = positions[symbol]
                pnl_record[symbol] = day_pnl.get(symbol, 0.0)
            equity_list.append({'date': date, 'equity': capital})
            position_records.append(pos_record)
            daily_pnl_records.append(pnl_record)
            prev_date = date
        
        equity_df = pd.DataFrame(equity_list).set_index('date')
        positions_df = pd.DataFrame(position_records).set_index('date') if position_records else pd.DataFrame()
        daily_pnl_df = pd.DataFrame(daily_pnl_records).set_index('date') if daily_pnl_records else pd.DataFrame()
        trades_df = pd.DataFrame(trade_list) if trade_list else pd.DataFrame()
        metrics = self._compute_metrics(equity_df, trades_df, daily_pnl_df, symbols)
        
        return {
            'equity_curve': equity_df, 'trades': trades_df, 'positions': positions_df,
            'daily_pnl': daily_pnl_df, 'metrics': metrics, 'forecasts': self.forecasts
        }
    
    def _compute_metrics(self, equity_df, trades_df, daily_pnl_df, symbols):
        equity = equity_df['equity']
        daily_returns = equity.pct_change().dropna()
        total_return = (equity.iloc[-1] / equity.iloc[0]) - 1
        
        # CAGR using actual calendar days
        first_date = pd.to_datetime(equity.index[0])
        last_date = pd.to_datetime(equity.index[-1])
        n_years = (last_date - first_date).days / 365.25
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / n_years) - 1 if n_years > 0 and equity.iloc[0] > 0 else 0
        
        # Sharpe annualized by actual trading days per year
        tdy = len(daily_returns) / n_years if n_years > 0 else 256
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(tdy) if daily_returns.std() > 0 else 0
        
        peak = equity.expanding().max()
        max_dd = ((equity - peak) / peak).min()
        
        if not daily_pnl_df.empty:
            tp = daily_pnl_df.sum(axis=1)
            gp, gl = tp[tp > 0].sum(), abs(tp[tp < 0].sum())
            profit_factor = gp / gl if gl > 0 else float('inf')
        else:
            profit_factor = 0
        
        n_trades = len(trades_df) if not trades_df.empty else 0
        annual_vol = daily_returns.std() * np.sqrt(tdy) if len(daily_returns) > 0 else 0
        total_friction = trades_df['friction'].sum() if not trades_df.empty and 'friction' in trades_df.columns else 0
        
        instrument_pnl = {}
        if not daily_pnl_df.empty:
            for s in symbols:
                if s in daily_pnl_df.columns:
                    instrument_pnl[s] = daily_pnl_df[s].sum()
        
        dr = daily_returns[daily_returns < 0]
        sortino = (daily_returns.mean() / dr.std()) * np.sqrt(tdy) if len(dr) > 0 and dr.std() > 0 else 0
        
        return {
            'total_return_pct': total_return * 100, 'cagr_pct': cagr * 100,
            'sharpe': sharpe, 'sortino': sortino,
            'max_drawdown_pct': max_dd * 100, 'calmar': cagr / abs(max_dd) if max_dd != 0 else 0,
            'profit_factor': profit_factor, 'annual_volatility_pct': annual_vol * 100,
            'return_dd_ratio': total_return / abs(max_dd) if max_dd != 0 else 0,
            'net_pnl': equity.iloc[-1] - equity.iloc[0],
            'total_trades': n_trades, 'total_friction': total_friction,
            'n_years': n_years, 'instrument_pnl': instrument_pnl,
            'start_equity': equity.iloc[0], 'end_equity': equity.iloc[-1],
            'rebalance_frequency': self.rebalance_freq,
        }
