import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GATConv
from torch_geometric.utils.convert import from_networkx
from sklearn.preprocessing import StandardScaler
import networkx as nx
import plotly.graph_objs as go

df = pd.read_csv("bank_transactions_data_2.csv")

numeric_cols = ['TransactionAmount', 'CustomerAge', 'TransactionDuration', 'LoginAttempts', 'AccountBalance']
scaler = StandardScaler()
df[numeric_cols] = scaler.fit_transform(df[numeric_cols])

features = torch.tensor(df[numeric_cols].values, dtype=torch.float32)

G = nx.Graph()
node_map = {}
current_idx = 0


def add_node(node_id):
    global current_idx
    if node_id not in node_map:
        node_map[node_id] = current_idx
        current_idx += 1


for _, row in df.iterrows():
    src = row['AccountID']
    dev = row['DeviceID']
    ip = row['IP Address']
    merch = row['MerchantID']

    for n in [src, dev, ip, merch]:
        add_node(n)

    G.add_edge(node_map[src], node_map[dev])
    G.add_edge(node_map[src], node_map[ip])
    G.add_edge(node_map[src], node_map[merch])

data = from_networkx(G)
num_nodes = data.num_nodes

x = torch.zeros((num_nodes, features.shape[1]))
node_features = {}

for _, row in df.iterrows():
    acc = row['AccountID']
    values = pd.to_numeric(row[numeric_cols], errors='coerce').fillna(0).values.astype('float32')
    row_values = torch.tensor(values, dtype=torch.float32)
    node_features[acc] = row_values

for node_id, idx in node_map.items():
    if node_id in node_features:
        x[idx] = node_features[node_id]

data.x = x


class GATEncoder(nn.Module):
    def __init__(self, in_channels, hidden_channels, out_channels, heads=2):
        super(GATEncoder, self).__init__()
        self.gat1 = GATConv(in_channels, hidden_channels, heads=heads)
        self.gat2 = GATConv(hidden_channels * heads, out_channels, heads=1)

    def forward(self, x, edge_index):
        x = F.elu(self.gat1(x, edge_index))
        x = self.gat2(x, edge_index)
        return x


class GATAutoencoder(nn.Module):
    def __init__(self, in_channels, hidden_channels, embedding_dim):
        super(GATAutoencoder, self).__init__()
        self.encoder = GATEncoder(in_channels, hidden_channels, embedding_dim)
        self.decoder = nn.Linear(embedding_dim, in_channels)

    def forward(self, x, edge_index):
        z = self.encoder(x, edge_index)
        x_hat = self.decoder(z)
        return x_hat, z


def reconstruction_loss(x, x_hat):
    return F.mse_loss(x_hat, x)


def anomaly_scores(x, x_hat):
    return ((x - x_hat) ** 2).mean(dim=1)


def train(model, data, epochs=100, lr=0.005):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        x_hat, _ = model(data.x, data.edge_index)
        loss = reconstruction_loss(data.x, x_hat)
        loss.backward()
        optimizer.step()
        if epoch % 10 == 0:
            print(f"Epoch {epoch}, Loss: {loss.item():.4f}")
    return model


def detect_anomalies(model, data, node_map, threshold=1.0):
    model.eval()
    x_hat, _ = model(data.x, data.edge_index)
    scores = anomaly_scores(data.x, x_hat)

    anomaly_mask = scores > threshold
    anomaly_indices = anomaly_mask.nonzero(as_tuple=False).view(-1)

    id_to_node = {v: k for k, v in node_map.items()}
    anomalies = [id_to_node[i.item()] for i in anomaly_indices]

    print(f"\nAnomalous Accounts Detected (score > {threshold}):")
    for i, idx in enumerate(anomaly_indices):
        node = id_to_node[idx.item()]
        fraud_score = scores[idx].item()
        print(f"{i + 1}. Account {node} (score: {fraud_score:.4f})")

    return anomalies


def visualize_graph_plotly(G, node_map, anomalies=None):
    pos = nx.spring_layout(G, seed=42)
    id_to_node = {v: k for k, v in node_map.items()}

    edge_x = []
    edge_y = []
    for edge in G.edges():
        x0, y0 = pos[edge[0]]
        x1, y1 = pos[edge[1]]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y,
        line=dict(width=0.5, color='#888'),
        hoverinfo='none',
        mode='lines'
    )

    node_x = []
    node_y = []
    node_text = []
    node_color = []

    for node in G.nodes():
        x, y = pos[node]
        node_x.append(x)
        node_y.append(y)
        label = str(id_to_node[node])
        node_text.append(label)
        if anomalies and label in anomalies:
            node_color.append('red')
        else:
            node_color.append('green')

    node_trace = go.Scatter(
        x=node_x, y=node_y,
        mode='markers+text',
        text=node_text,
        textposition="bottom center",
        hoverinfo='text',
        marker=dict(
            color=node_color,
            size=10,
            line_width=1
        )
    )

    fig = go.Figure(
        data=[edge_trace, node_trace],
        layout=go.Layout(
            title=dict(text='Interactive Graph with Anomaly Highlighting', x=0.5),
            showlegend=False,
            hovermode='closest',
            margin=dict(b=20, l=5, r=5, t=40),
            xaxis=dict(showgrid=False, zeroline=False),
            yaxis=dict(showgrid=False, zeroline=False)
        )
    )

    fig.show()


unique_nodes = set(df['AccountID'].unique()).union(set(df['DeviceID'].unique())).union(
    set(df['IP Address'].unique())).union(set(df['MerchantID'].unique()))
num_nodes = len(unique_nodes)
print(f"Toplam düğüm sayısı: {num_nodes}")

model = GATAutoencoder(in_channels=features.shape[1], hidden_channels=16, embedding_dim=8)
model = train(model, data)

top_anomalies = detect_anomalies(model, data, node_map, threshold=1.0)
visualize_graph_plotly(G, node_map, anomalies=set(top_anomalies))
