```mermaid
flowchart TD
    classDef customer fill:#DCE6F1,stroke:#1F3864,stroke-width:1px,color:#1a1a1a;
    classDef staff fill:#DCEEEE,stroke:#2E8B8B,stroke-width:1px,color:#1a1a1a;
    classDef system fill:#F2F2F2,stroke:#595959,stroke-width:1px,color:#1a1a1a;
    classDef decision fill:#FBE9DA,stroke:#B36B00,stroke-width:1px,color:#1a1a1a;
    classDef terminal fill:#1F3864,stroke:#1F3864,stroke-width:1px,color:#ffffff;

    subgraph LANE1["Customer"]
        C1["Upload PO<br/>(Status: PO_Created)"]
        C2["Upload Revised PO<br/>(Version v(n) -> v(n+1))"]
    end

    subgraph LANE2["System (Smart-SO)"]
        S1["Auto-generate<br/>Invoice PDF"]
        S2["Send Email Alert<br/>to Staff"]
        S3["AI Analyzer:<br/>Compare PO vs Quotation<br/>(Status: Pending_Staff_Review)"]
        S5["ERP/MES:<br/>Auto-create Sales Order"]
    end

    subgraph LANE3["Staff / Operations"]
        T1["Upload Quotation<br/>& Trigger Audit"]
        T2{"Discrepancy<br/>Found?"}
        T3["Confirm & Pass"]
        T4["Request Amendment<br/>(Status: Pending_Amendment)"]
    end

    E1["Order_Confirmed<br/>(Ticket Closed)"]

    C1 --> S1 --> S2 --> T1 --> S3 --> T2
    T2 -- "Match / OK" --> T3 --> S5 --> E1
    T2 -- "Mismatch" --> T4 --> C2 --> S3

    class C1,C2 customer
    class T1,T2,T3,T4 staff
    class S1,S2,S3,S5 system
    class T2 decision
    class E1 terminal
